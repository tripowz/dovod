"""Работа без своего сервера: Telegram присылает сообщения, Supabase будит агентов.

Проверяется то, что ломается в таком режиме: поддельный запрос на webhook,
повторная доставка одного сообщения, чат, выключенный руководителем,
переписка Telegram Business (клиент и владелец — ответ клиенту), два прохода
по расписанию одновременно и сводка, ушедшая дважды за день.

    python tests/test_serverless.py                        — на SQLite
    TEST_DATABASE_URL=postgresql+asyncpg://… python tests/test_serverless.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOKEN = "123456:TEST-TOKEN-NOT-REAL"
_tmp = Path(tempfile.mkdtemp(prefix="dovod-sls-"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{(_tmp / 's.db').as_posix()}"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["CRON_SECRET"] = "cron-secret-test"
os.environ["SESSION_SECRET"] = "session-secret-test"
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")
os.environ["ANTHROPIC_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app import auth, state  # noqa: E402
from app.bot import telegram as bot  # noqa: E402
from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.ingest import webhook  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    TELEGRAM_SERVICE_ID, Chat, ChatKind, Message, Proposal, ProposalKind, Staff, Ticket, TicketStatus,
)

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


OWNER = 900_000_001  # владелец служебного Business-аккаунта
CLIENT = 700_000_002  # отель пишет в личку
T0 = int(time.time())


def tg_msg(chat: dict, mid: int, text: str, sender: int, dt: int = 0, **extra) -> dict:
    return {"message_id": mid, "date": T0 - dt, "chat": chat, "text": text,
            "from": {"id": sender, "first_name": "Человек"}, **extra}


GROUP = {"id": -100500, "type": "supergroup", "title": "Noble x SmartBooking"}
SUPPORT = {"id": -100600, "type": "supergroup", "title": "SmartBooking Support"}
DM = {"id": CLIENT, "type": "private", "first_name": "Ajwa Hotel", "last_name": "Akmal"}


async def main() -> None:
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await init_db()
    H = {"X-Telegram-Bot-Api-Secret-Token": webhook.webhook_secret()}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        print("=" * 62)
        print("WEBHOOK: ГРУППЫ")
        print("=" * 62)
        r = await c.post("/api/telegram/webhook", json={"update_id": 1, "message": tg_msg(GROUP, 1, "x", 5)})
        check("без секрета — 401", r.status_code == 401)
        r = await c.post("/api/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"})
        check("с чужим секретом — 401", r.status_code == 401)

        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 2, "my_chat_member": {
            "chat": SUPPORT, "new_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True}}}})
        check("бота добавили в группу — чат заведён", r.json()["result"] == "group_added")

        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 3, "message": tg_msg(GROUP, 10, "у нас не приходят брони с Booking", 5)})
        check("сообщение из группы сохранено", r.json()["result"] == "stored", r.text)
        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 3, "message": tg_msg(GROUP, 10, "у нас не приходят брони с Booking", 5)})
        check("повторная доставка не дублирует", r.json()["result"] == "skipped")
        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 4, "message": {
            "message_id": 11, "date": T0, "chat": GROUP, "from": {"id": 5}, "new_chat_members": [{"id": 6}]}})
        check("служебное «вступил в группу» не сохраняется", r.json()["result"] == "skipped")
        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 5, "message": {
            "message_id": 12, "date": T0, "chat": GROUP, "from": {"id": 5}, "photo": [{}], "caption": "скрин ошибки"}})
        check("фото с подписью сохраняется", r.json()["result"] == "stored")

        async with SessionLocal() as s:
            chats = {ch.tg_chat_id: ch for ch in (await s.execute(select(Chat))).scalars()}
            msgs = (await s.execute(select(Message).order_by(Message.id))).scalars().all()
        g = chats[GROUP["id"]]
        check("группа включена с основанием", g.enabled and g.consent_note == webhook.CONSENT_GROUP)
        check("тип по названию: поддержка", chats[SUPPORT["id"]].kind == ChatKind.support)
        check("текст, автор, время", msgs[0].text.startswith("у нас") and msgs[0].tg_user_id == 5 and msgs[0].sent_at is not None)
        check("подпись к фото — текст, вложение отмечено", msgs[1].text == "скрин ошибки" and msgs[1].media_kind == "photo")

        async with SessionLocal() as s:
            (await s.get(Chat, g.id)).enabled = False
            await s.commit()
        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 6, "message": tg_msg(GROUP, 13, "ещё", 5)})
        check("выключенный руководителем чат не читается", r.json()["result"] == "skipped")

        print()
        print("=" * 62)
        print("WEBHOOK: TELEGRAM BUSINESS")
        print("=" * 62)
        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 7, "business_connection": {
            "id": "bc1", "user": {"id": OWNER, "first_name": "Smartbooking"}, "user_chat_id": OWNER,
            "date": T0, "is_enabled": True}})
        async with SessionLocal() as s:
            owner = await webhook.owner_id(s)
        check("владелец Business-аккаунта запомнен", owner == OWNER, f"{owner}")

        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 8, "business_message":
            tg_msg(DM, 100, "Добрый день, у нас касса не печатает чек", CLIENT, dt=600, business_connection_id="bc1")})
        check("личка клиента сохранена", r.json()["result"] == "stored")
        r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 9, "business_message":
            tg_msg({"id": TELEGRAM_SERVICE_ID, "type": "private", "first_name": "Telegram"}, 1, "Код входа 12345", TELEGRAM_SERVICE_ID)})
        check("служебный чат с кодами — никогда", r.json()["result"] == "skipped")
        async with SessionLocal() as s:
            dm = (await s.execute(select(Chat).where(Chat.tg_chat_id == CLIENT))).scalar_one()
            none = (await s.execute(select(Chat).where(Chat.tg_chat_id == TELEGRAM_SERVICE_ID))).scalar_one_or_none()
        check("личка — тип «личная», с основанием Business", dm.kind == ChatKind.private and dm.consent_note == webhook.CONSENT_BUSINESS)
        check("название лички — имя собеседника", dm.title == "Ajwa Hotel Akmal", dm.title)
        check("чат с кодами не заведён", none is None)

        print()
        print("=" * 62)
        print("WEBHOOK: КОМАНДЫ БОТУ")
        print("=" * 62)
        seen: list[dict] = []

        async def fake_handle(update: dict) -> None:
            seen.append(update)

        real = bot.handle_update
        bot.handle_update = fake_handle
        try:
            r = await c.post("/api/telegram/webhook", headers=H, json={"update_id": 10, "message":
                tg_msg({"id": OWNER, "type": "private"}, 1, "/id", OWNER)})
        finally:
            bot.handle_update = real
        check("личка самому боту — в обработчик команд", r.json()["result"] == "bot" and len(seen) == 1)

        print()
        print("=" * 62)
        print("ПРОХОД ПО РАСПИСАНИЮ")
        print("=" * 62)
        r = await c.post("/api/cron/tick")
        check("без секрета — 401", r.status_code == 401)
        r = await c.post("/api/cron/tick", headers={"Authorization": "Bearer wrong-secret"})
        check("с чужим секретом — 401", r.status_code == 401)

        # Заявка из лички и ответ владельца аккаунта — уже в Telegram, не в Доводе.
        async with SessionLocal() as s:
            asked = (await s.execute(select(Message).where(Message.tg_msg_id == 100))).scalar_one()
            s.add(Ticket(ref="T-500", message_id=asked.id, text=asked.text, status=TicketStatus.new,
                         created_at=asked.sent_at))
            await s.commit()
        await c.post("/api/telegram/webhook", headers=H, json={"update_id": 11, "business_message":
            tg_msg(DM, 101, "Здравствуйте! Смотрим, через 5 минут отпишемся", OWNER, dt=300, business_connection_id="bc1")})

        sent: list[str] = []

        async def fake_digest() -> int:
            sent.append("digest")
            return 1

        real_digest = bot.send_morning_digest
        bot.send_morning_digest = fake_digest
        from app import worker

        real_esc = worker.check_escalations

        async def no_esc() -> int:
            return 0

        worker.check_escalations = no_esc
        from app.config import settings

        settings.digest_hour_local = 0  # сводка «уже пора» в любое время теста
        try:
            CH = {"Authorization": "Bearer cron-secret-test"}
            r = await c.post("/api/cron/tick", headers=CH)
            out = r.json()
            check("проход отработал", r.status_code == 200 and out["ok"], r.text[:160])
            async with SessionLocal() as s:
                t = (await s.execute(select(Ticket).where(Ticket.ref == "T-500"))).scalar_one()
            check("ответ владельца в Telegram снял ожидание заявки", t.first_reply_at is not None)
            check("сводка ушла", sent == ["digest"])
            r = await c.post("/api/cron/tick", headers=CH)
            check("второй раз за день сводка не уходит", sent == ["digest"], f"{sent}")

            async with SessionLocal() as s:
                await state.put(s, "tick_lock", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
                await s.commit()
            r = await c.post("/api/cron/tick", headers=CH)
            check("пока идёт прошлый проход — второй не начинается", "skipped" in r.json(), r.text[:100])
            async with SessionLocal() as s:
                old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
                await state.put(s, "tick_lock", old.isoformat())
                await s.commit()
            r = await c.post("/api/cron/tick", headers=CH)
            check("зависший замок через время снимается", "skipped" not in r.json(), r.text[:100])
        finally:
            bot.send_morning_digest = real_digest
            worker.check_escalations = real_esc

        print()
        print("=" * 62)
        print("САЙТ В РЕЖИМЕ VERCEL")
        print("=" * 62)
        async with SessionLocal() as s:
            s.add(Staff(id=1, tg_user_id=-1, name="Бекзод", is_manager=True))
            await s.commit()
        c.cookies.set(auth.SESSION_COOKIE, auth.make_session(1))
        r = await c.get("/api/chats/full")
        check("экран «Чаты»: режим webhook", r.json()["mode"] == "webhook", r.json().get("mode"))
        r = await c.post("/api/sync", headers={"X-Dovod": "1"})
        check("«Разобрать сейчас» — проход прямо в запросе", r.status_code == 200 and "Разобрано" in r.json()["message"], r.text[:100])

    async with SessionLocal() as s:
        await s.execute(delete(Proposal))
        await s.commit()
    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
