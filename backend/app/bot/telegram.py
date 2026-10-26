"""Бот «Довода»: всё исходящее идёт через него.

Telethon-сессия только читает (см. `config.telethon_read_only`). Писать от
имени живого сотрудника нельзя, поэтому кнопка мини-аппа, сводка и
эскалации отправляются ботом: машинное сообщение видно как машинное.

Работает через Bot API напрямую (httpx), без фреймворка: нужно пять методов,
и зависимость ради них дороже, чем эти полторы сотни строк.

Запуск: `python -m app.bot.telegram`
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import service
from ..config import settings
from ..db import SessionLocal, init_db
from ..models import Hotel, Staff, Task, Ticket

log = logging.getLogger("dovod.bot")
# httpx пишет в лог полный URL запроса, а в URL Bot API лежит токен. На уровне
# INFO он оказывался бы в каждом лог-файле — то есть утекал бы тихо и навсегда.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.telegram.org/bot{token}/{method}"


class BotError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def call(method: str, **params) -> object:
    if not settings.bot_token:
        raise BotError("BOT_TOKEN не задан")
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(API.format(token=settings.bot_token, method=method), json=params)
    try:
        data = r.json()
    except ValueError:
        raise BotError(f"{method}: Telegram ответил не JSON (HTTP {r.status_code})") from None
    if not data.get("ok"):
        raise BotError(f"{method}: {data.get('description', 'неизвестная ошибка')}")
    return data["result"]


def _app_button(text: str = "Открыть Довод") -> dict | None:
    # Telegram не откроет мини-апп по http или localhost. Кнопку, которая
    # никуда не ведёт, лучше не показывать вовсе.
    if not settings.miniapp_url.startswith("https://"):
        return None
    return {"inline_keyboard": [[{"text": text, "web_app": {"url": settings.miniapp_url}}]]}


async def send(chat_id: int, text: str, *, with_app: bool = False) -> None:
    """Отправляет простой текст, без разметки.

    Разметку не используем намеренно: заголовки задач пишут люди, и одно
    подчёркивание в них ломает разбор Markdown — Telegram отвергает сообщение
    целиком. Доставленная сводка важнее жирного шрифта.
    """
    params: dict = {
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if with_app and (kb := _app_button()):
        params["reply_markup"] = kb
    await call("sendMessage", **params)


async def setup() -> None:
    """Список команд и кнопка мини-аппа в меню чата с ботом."""
    await call(
        "setMyCommands",
        commands=[
            {"command": "start", "description": "Открыть Довод"},
            {"command": "digest", "description": "Сводка прямо сейчас"},
            {"command": "report", "description": "Отчёт по сотрудникам за неделю"},
            {"command": "id", "description": "Мой Telegram ID"},
        ],
    )
    if settings.miniapp_url.startswith("https://"):
        await call(
            "setChatMenuButton",
            menu_button={"type": "web_app", "text": "Довод", "web_app": {"url": settings.miniapp_url}},
        )
    else:
        log.warning(
            "MINIAPP_URL не https (%r): кнопка мини-аппа не установлена — "
            "Telegram не открывает http и localhost",
            settings.miniapp_url,
        )


async def compose_digest(staff: Staff) -> str:
    async with SessionLocal() as s:
        tasks = (
            await s.execute(
                select(Task).options(selectinload(Task.assignee), selectinload(Task.author))
            )
        ).scalars().all()
        tickets = (
            await s.execute(select(Ticket).options(selectinload(Ticket.hotel)))
        ).scalars().all()
        hotels = (await s.execute(select(Hotel))).scalars().all()
    d = service.build_digest(list(tasks), list(tickets), list(hotels), staff, _now())
    return service.digest_text(d).replace("*", "")


async def compose_staff_report(days: int = 7) -> str:
    """Текст недельного отчёта по сотрудникам для Telegram.

    Грузит историю задач вместе с ними: возвраты на доработку считаются из неё,
    и до-загрузка по одной задаче превратила бы отчёт в сотни запросов.
    """
    since = _now() - timedelta(days=days)
    async with SessionLocal() as s:
        tasks = (
            await s.execute(
                select(Task).options(
                    selectinload(Task.assignee), selectinload(Task.events)
                )
            )
        ).scalars().all()
        tickets = (await s.execute(select(Ticket))).scalars().all()
        staff_rows = (
            await s.execute(select(Staff).where(Staff.active.is_(True)))
        ).scalars().all()
    rows = service.build_staff_report(
        list(staff_rows), list(tasks), list(tickets), _now(), since
    )
    return service.staff_report_text(rows, days).replace("*", "")


async def _staff_by_tg(tg_id: int) -> Staff | None:
    async with SessionLocal() as s:
        return (
            await s.execute(select(Staff).where(Staff.tg_user_id == tg_id))
        ).scalar_one_or_none()


async def handle_update(update: dict) -> None:
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    # В группах бот молчит. Чаты читает Telethon; бот, отвечающий в рабочих
    # группах, быстро становится шумом, который все отключают.
    if chat.get("type") != "private":
        return

    text = (msg.get("text") or "").strip()
    tg_id = int((msg.get("from") or {}).get("id", 0))

    if text.startswith("/id"):
        await send(chat["id"], f"Ваш Telegram ID: {tg_id}")
        return

    staff = await _staff_by_tg(tg_id)
    if staff is None or not staff.active:
        # Самый частый вопрос при подключении — «почему меня не пускает».
        # Отвечаем сразу тем, что нужно руководителю, чтобы добавить человека.
        await send(
            chat["id"],
            "Вас нет в списке сотрудников «Довода».\n"
            f"Передайте руководителю ваш Telegram ID: {tg_id}",
        )
        return

    if text.startswith("/digest"):
        if not staff.is_manager:
            await send(
                chat["id"],
                "Сводка по команде доступна руководителю. Ваши задачи — в приложении.",
                with_app=True,
            )
            return
        await send(chat["id"], await compose_digest(staff), with_app=True)
        return

    if text.startswith("/report"):
        if not staff.is_manager:
            await send(
                chat["id"],
                "Отчёт по команде доступен руководителю. Ваши задачи — в приложении.",
                with_app=True,
            )
            return
        await send(chat["id"], await compose_staff_report(), with_app=True)
        return

    await send(
        chat["id"],
        f"{staff.name}, всё в приложении: входящие из чатов, задачи и заявки отелей.",
        with_app=True,
    )


async def send_morning_digest() -> int:
    """Утренняя сводка всем руководителям. Возвращает число доставленных."""
    async with SessionLocal() as s:
        managers = (
            await s.execute(
                select(Staff).where(Staff.is_manager.is_(True), Staff.active.is_(True))
            )
        ).scalars().all()
    sent = 0
    for m in managers:
        try:
            await send(m.tg_user_id, await compose_digest(m), with_app=True)
            sent += 1
        except BotError as exc:
            # Чаще всего руководитель ни разу не писал боту: Telegram запрещает
            # боту начинать диалог первым. Лечится одним /start — и это должно
            # быть видно в логе, а не пропадать молча.
            log.warning("сводка для %s не ушла: %s", m.name, exc)
    return sent


async def send_weekly_report() -> int:
    """Недельный отчёт по сотрудникам всем руководителям. Возвращает число доставленных.

    Один и тот же текст на всех: отчёт о команде не зависит от того, кто из
    руководителей его открыл, — в отличие от сводки, где «ждёт вашей приёмки»
    считается на конкретного человека.
    """
    async with SessionLocal() as s:
        managers = (
            await s.execute(
                select(Staff).where(Staff.is_manager.is_(True), Staff.active.is_(True))
            )
        ).scalars().all()
    if not managers:
        log.warning("недельный отчёт некому отправить — нет активных руководителей")
        return 0
    text = await compose_staff_report()
    sent = 0
    for m in managers:
        try:
            await send(m.tg_user_id, text, with_app=True)
            sent += 1
        except BotError as exc:
            log.warning("отчёт для %s не ушёл: %s", m.name, exc)
    return sent


async def run_polling() -> None:
    """Long polling.

    Для пилота на одной машине проще вебхука: публичный HTTPS-адрес нужен
    только самому мини-аппу, а не бэкенду бота.
    """
    await call("deleteWebhook")  # иначе getUpdates отвечает 409
    offset = 0
    log.info("бот слушает обновления")
    while True:
        try:
            updates = await call(
                "getUpdates", offset=offset, timeout=30, allowed_updates=["message"]
            )
        except (BotError, httpx.HTTPError) as exc:
            log.warning("getUpdates: %s — повтор через 5 с", exc)
            await asyncio.sleep(5)
            continue
        for u in updates:  # type: ignore[union-attr]
            offset = u["update_id"] + 1
            try:
                await handle_update(u)
            except Exception:
                log.exception("не обработано обновление %s", u.get("update_id"))


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    await init_db()
    await setup()
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
