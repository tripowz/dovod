"""Фоновый процесс «Довода».

    python -m app.worker

Раз в ANALYZE_INTERVAL_MINUTES читает включённые чаты и разбирает новые
сообщения; раз в пять минут проверяет правила эскалации; в DIGEST_HOUR_LOCAL
отправляет руководителям утреннюю сводку.

Без Telethon-сессии процесс всё равно полезен: разбирает уже сохранённые
сообщения и следит за обещаниями клиентам. Отсутствие одной части не должно
выключать остальные молча — поэтому каждая пишет в лог, почему не работает.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import service
from .agents.analyze import OPEN_STATUSES, analyze_pending
from .bot.telegram import BotError, send, send_morning_digest
from .config import settings
from .db import SessionLocal, init_db
from .ingest.reader import fetch_new, make_client, sync_dialogs
from .models import Escalation, Hotel, Rule, Staff, Task, Ticket, TicketStatus

log = logging.getLogger("dovod.worker")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def check_escalations() -> int:
    """Проверяет включённые правила и будит руководителя. Возвращает число эскалаций.

    Эскалация — единственное, что система делает без человека. Всё остальное
    она предлагает; здесь наоборот, потому что нарушенное обещание, о котором
    никто не узнал, и есть та дыра, ради которой всё затевалось.
    """
    ts = _now()
    fired = 0
    async with SessionLocal() as s:
        rules = {r.code: r for r in (await s.execute(select(Rule))).scalars()}
        managers = (
            await s.execute(
                select(Staff).where(Staff.is_manager.is_(True), Staff.active.is_(True))
            )
        ).scalars().all()
        recent = (
            await s.execute(select(Escalation).where(Escalation.created_at >= ts - timedelta(days=8)))
        ).scalars().all()

        def on(code: str) -> bool:
            r = rules.get(code)
            return bool(r and r.enabled)

        found: list[tuple[tuple[str, str, int], str]] = []

        if on("first_reply_late"):
            tickets = (
                await s.execute(
                    select(Ticket)
                    .options(selectinload(Ticket.hotel))
                    .where(
                        Ticket.first_reply_at.is_(None),
                        Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
                    )
                )
            ).scalars().all()
            for t in tickets:
                waited = int((ts - t.created_at).total_seconds() // 60)
                if waited >= settings.escalate_first_reply_minutes:
                    hotel = t.hotel.name if t.hotel else "клиент"
                    found.append((
                        ("first_reply_late", "ticket", t.id),
                        f"{t.ref}: {hotel} ждёт первого ответа {waited} мин при обещанных "
                        f"{settings.sla_first_reply_minutes}. Ответьте клиенту.",
                    ))

        if on("task_idle"):
            tasks = (
                await s.execute(
                    select(Task).options(selectinload(Task.assignee)).where(Task.status.in_(OPEN_STATUSES))
                )
            ).scalars().all()
            for t in tasks:
                idle = t.idle_days(ts)
                if idle >= settings.escalate_idle_days:
                    who = t.assignee.name if t.assignee else "без исполнителя"
                    found.append((("task_idle", "task", t.id), f"{t.ref} стоит {idle} дн. — {who}: {t.title}"))

        if on("hotel_third_ticket"):
            week = ts.isocalendar()[1]
            counts = (
                await s.execute(
                    select(Ticket.hotel_id, func.count(Ticket.id))
                    .where(Ticket.created_at >= ts - timedelta(days=7), Ticket.hotel_id.is_not(None))
                    .group_by(Ticket.hotel_id)
                )
            ).all()
            names = {h.id: h.name for h in (await s.execute(select(Hotel))).scalars()}
            for hotel_id, n in counts:
                if n >= 3:
                    # Номер недели в ключе: иначе отель, однажды попавший в
                    # эскалацию, больше никогда бы в неё не попал.
                    found.append((
                        ("hotel_third_ticket", "hotel", hotel_id * 100 + week),
                        f"{names.get(hotel_id, 'Отель')}: {n} обращений за неделю. "
                        "Клиент уходит в риск — посмотрите карточку.",
                    ))

        for key, text in found:
            if service.already_escalated(list(recent), key):
                continue
            delivered_to = None
            for m in managers:
                try:
                    await send(m.tg_user_id, f"Эскалация. {text}", with_app=True)
                    delivered_to = delivered_to or m.tg_user_id
                except BotError as exc:
                    log.warning("эскалация для %s не ушла: %s", m.name, exc)
            if delivered_to is None:
                # Никому не доставлено — не записываем, чтобы повторить на
                # следующем проходе. Записанная, но не доставленная эскалация —
                # то же молчание, только с отметкой «сделано».
                continue
            s.add(Escalation(
                rule_code=key[0], subject_kind=key[1], subject_id=key[2],
                notified_tg_user_id=delivered_to, text=text, created_at=ts,
            ))
            fired += 1

        await s.flush()
        # Счётчик пересчитывается, а не наращивается: иначе «за неделю» со
        # временем превратилось бы в «за всё время».
        for code, r in rules.items():
            r.hits_7d = (
                await s.execute(
                    select(func.count(Escalation.id)).where(
                        Escalation.rule_code == code, Escalation.created_at >= ts - timedelta(days=7)
                    )
                )
            ).scalar() or 0
        await s.commit()

    if found and not managers:
        log.warning("есть %d нарушений, но нет ни одного руководителя — будить некого", len(found))
    return fired


async def cycle(client) -> None:
    if client is not None:
        try:
            async with SessionLocal() as s:
                added = await sync_dialogs(client, s)
                stored = await fetch_new(client, s)
            if added or stored:
                log.info("новых чатов: %d, новых сообщений: %d", added, stored)
        except Exception:
            log.exception("чтение чатов упало")
    try:
        async with SessionLocal() as s:
            stats = await analyze_pending(s)
        if stats["messages"]:
            log.info("разбор: %s", stats)
    except Exception:
        log.exception("разбор упал")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    await init_db()

    client = None
    if (
        settings.telegram_api_id
        and settings.telegram_api_hash
        and Path(settings.telegram_session_path).exists()
    ):
        client = make_client()
        await client.connect()
        if not await client.is_user_authorized():
            log.error("сессия Telethon не авторизована — выполните: python -m app.ingest.login")
            await client.disconnect()
            client = None
    else:
        log.warning(
            "Telethon не настроен или нет сессии — чтение чатов выключено. "
            "Разбор сохранённых сообщений и эскалации работают."
        )
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY не задан — сообщения копятся без разбора")
    if not settings.bot_token:
        log.warning("BOT_TOKEN не задан — сводка и эскалации не отправляются")

    tz = ZoneInfo(settings.timezone)
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(
        cycle, "interval", args=[client], minutes=settings.analyze_interval_minutes,
        next_run_time=datetime.now(tz), max_instances=1, coalesce=True,
    )
    sched.add_job(check_escalations, "interval", minutes=5, max_instances=1, coalesce=True)
    sched.add_job(send_morning_digest, "cron", hour=settings.digest_hour_local, minute=0, max_instances=1)
    sched.start()
    log.info("воркер запущен")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
