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
from .agents.diagnose import run_diagnosis
from .agents.learn import distill_task
from .bot.telegram import BotError, send, send_morning_digest, send_weekly_report
from .config import settings
from .db import SessionLocal, init_db
from .models import (
    Chat,
    ChatKind,
    Diagnosis,
    Escalation,
    Hotel,
    HotelContact,
    Knowledge,
    Message,
    Module,
    Proposal,
    ProposalKind,
    ProposalState,
    Rule,
    Staff,
    Task,
    TaskStatus,
    TaskType,
    Ticket,
    TicketStatus,
)

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


async def create_tickets_from_chat() -> int:
    """Превращает запросы отелей из support-чатов в обращения (Ticket).

    Источник — не отдельный разбор, а уже готовая разметка: сообщение из
    support-чата, которое анализатор счёл новым запросом (`new_task`), и есть
    обращение клиента. Берём только support-чаты: такой же `new_task` из
    рабочей группы команды — это внутренняя задача, а не обращение, и SLA ему
    не нужен.

    Дедуп — по `message_id`: одно обращение на сообщение, повторный проход не
    плодит заявки. `created_at` берём из времени сообщения, а не из «сейчас»:
    клиент ждёт с момента, когда написал, и SLA обязан считаться оттуда, иначе
    опоздание маскируется задержкой разбора.
    """
    if not settings.auto_ticket_enabled:
        return 0
    created = 0
    async with SessionLocal() as s:
        taken = select(Ticket.message_id).where(Ticket.message_id.is_not(None))
        rows = (
            await s.execute(
                select(Message, Proposal)
                .join(Chat, Chat.id == Message.chat_id)
                .join(Proposal, Proposal.message_id == Message.id)
                .where(
                    Chat.kind.in_(
                        [ChatKind.support, ChatKind.private]
                        if settings.private_as_support
                        else [ChatKind.support]
                    ),
                    Chat.enabled.is_(True),
                    Proposal.kind == ProposalKind.new_task,
                    Proposal.state.in_([ProposalState.pending, ProposalState.applied]),
                    Message.id.notin_(taken),
                    Message.sent_at
                    >= datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(hours=settings.fresh_hours),
                )
                .order_by(Message.sent_at)
                .limit(settings.auto_ticket_max_per_cycle)
            )
        ).all()
        if not rows:
            return 0

        contacts = {
            c.tg_user_id: c
            for c in (
                await s.execute(
                    select(HotelContact).where(HotelContact.tg_user_id.is_not(None))
                )
            ).scalars()
        }
        base = (await s.execute(select(func.count(Ticket.id)))).scalar() or 0

        for i, (m, p) in enumerate(rows, start=1):
            draft = p.draft or {}
            try:
                typ = TaskType(draft.get("type") or "")
            except ValueError:
                typ = TaskType.question
            try:
                mod = Module(draft.get("module") or "")
            except ValueError:
                mod = Module.unknown
            contact = contacts.get(m.tg_user_id)
            # Отель — по автору, а если автор неизвестен — по чату: в чате
            # «X & Smartbooking» пишет X, кто бы это ни был.
            chat_hotel = (await s.get(Chat, m.chat_id)).hotel_id
            s.add(
                Ticket(
                    ref=f"T-{100 + base + i}",
                    hotel_id=contact.hotel_id if contact else chat_hotel,
                    message_id=m.id,
                    author_name=m.author_name or (contact.name if contact else ""),
                    author_role=contact.role if contact else "",
                    text=m.text,
                    type=typ,
                    module=mod,
                    status=TicketStatus.new,
                    created_at=m.sent_at,
                )
            )
            created += 1
        await s.commit()
    if created:
        log.info("создано обращений из чата: %d", created)
    return created


async def detect_replies(self_id: int | None) -> int:
    """Отмечает ответ клиенту, если его дали прямо в Telegram.

    Отвечают отелям в самом чате, а не кнопкой в мини-аппе. Без этого заявка
    висела бы «без ответа», и через полчаса руководитель получал бы ложную
    эскалацию о нарушенном обещании. Ответом считается первое сообщение в том
    же чате после обращения — от сотрудника или от служебного аккаунта,
    которым читаются чаты.
    """
    async with SessionLocal() as s:
        staff = {
            st.tg_user_id: st
            for st in (await s.execute(select(Staff).where(Staff.active.is_(True)))).scalars()
        }
        ours = set(staff) | ({self_id} if self_id else set())
        if not ours:
            return 0
        rows = (
            await s.execute(
                select(Ticket, Message)
                .join(Message, Message.id == Ticket.message_id)
                .where(
                    Ticket.first_reply_at.is_(None),
                    Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
                )
            )
        ).all()
        marked = 0
        for ticket, asked in rows:
            reply = (
                await s.execute(
                    select(Message)
                    .where(
                        Message.chat_id == asked.chat_id,
                        Message.sent_at > asked.sent_at,
                        Message.tg_user_id.in_(ours),
                    )
                    .order_by(Message.sent_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if reply is None:
                continue
            ticket.first_reply_at = reply.sent_at
            who = staff.get(reply.tg_user_id)
            if who is not None and ticket.assignee_id is None:
                ticket.assignee_id = who.id
            if ticket.status == TicketStatus.new:
                ticket.status = TicketStatus.working
            marked += 1
        await s.commit()
    if marked:
        log.info("ответ клиенту найден в чате: %d заявок", marked)
    return marked


async def auto_diagnose() -> int:
    """Ставит диагноз обращениям типа «баг/проблема», у которых его ещё нет.

    Разбирает только открытые обращения: диагноз закрытому уже никому не нужен.
    За проход — не больше потолка из конфига: диагноз это вызов модели, и
    непродиагностированный хвост спокойно уедет на следующий проход, а не
    упрётся в лимит разом.
    """
    if not settings.auto_diagnose_enabled or not settings.anthropic_api_key:
        return 0
    async with SessionLocal() as s:
        diagnosed = select(Diagnosis.ticket_id).where(Diagnosis.ticket_id.is_not(None))
        refs = (
            await s.execute(
                select(Ticket.ref)
                .where(
                    Ticket.type.in_([TaskType.bug, TaskType.problem]),
                    Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
                    Ticket.id.notin_(diagnosed),
                )
                .order_by(Ticket.created_at.desc())
                .limit(settings.auto_diagnose_max_per_cycle)
            )
        ).scalars().all()

    done = 0
    for ref in refs:
        # Своя сессия на обращение: run_diagnosis коммитит сам, и одна упавшая
        # модель не должна откатывать уже поставленные диагнозы.
        try:
            async with SessionLocal() as s:
                if await run_diagnosis(s, ref) is not None:
                    done += 1
        except Exception:
            log.exception("авто-диагноз %s упал", ref)
    if done:
        log.info("авто-диагноз: поставлено %d", done)
    return done


async def learn_from_closed_tasks() -> int:
    """Пополняет базу знаний из закрытых задач-багов/проблем.

    Берёт задачи в статусе «Готово», по которым ещё нет записи базы (ни урока,
    ни метки «без урока»), и отдаёт агенту на дистилляцию. Потолок за проход
    бережёт бюджет: закрытые задачи не горят, хвост разберётся позже. Возвращает
    число задач, из которых извлечён многоразовый урок.
    """
    if not settings.learn_from_tasks_enabled or not settings.anthropic_api_key:
        return 0
    async with SessionLocal() as s:
        processed = select(Knowledge.task_ref).where(Knowledge.task_ref != "")
        refs = (
            await s.execute(
                select(Task.ref)
                .where(
                    Task.status == TaskStatus.done,
                    Task.type.in_([TaskType.bug, TaskType.problem]),
                    Task.ref.notin_(processed),
                )
                .order_by(Task.last_moved_at.desc())
                .limit(settings.learn_max_per_cycle)
            )
        ).scalars().all()

    learned = 0
    for ref in refs:
        try:
            async with SessionLocal() as s:
                if await distill_task(s, ref) is not None:
                    learned += 1
        except Exception:
            log.exception("обучение по %s упало", ref)
    if refs:
        log.info("обучение из задач: разобрано %d, с уроком %d", len(refs), learned)
    return learned


# Telegram ID аккаунта, которым читаются чаты: его сообщения — ответы клиенту.
SELF_ID: dict[str, int] = {}
# Подключённый Telethon — для сайта, когда всё запущено одним процессом
# (python -m app.run): «Обновить сейчас» и загрузка истории идут через него.
LIVE: dict[str, object] = {}
# Что сейчас идёт в фоне по кнопке с сайта.
BUSY: dict[str, object] = {}


async def cycle(client) -> None:
    """Проход воркера с Telethon: забрать новое из чатов и прогнать агентов."""
    from .ingest.reader import fetch_new, sync_dialogs  # Telethon — только здесь

    if client is not None:
        try:
            async with SessionLocal() as s:
                added, tops = await sync_dialogs(client, s)
                stored = await fetch_new(client, s, tops)
            if added or stored:
                log.info("новых чатов: %d, новых сообщений: %d", added, stored)
        except Exception:
            log.exception("чтение чатов упало")
    await run_agents(SELF_ID.get("id"))


async def run_agents(self_id: int | None, budget_s: float | None = None) -> dict:
    """Агенты по уже сохранённым сообщениям. Один проход, без чтения чатов.

    Его вызывает и воркер с Telethon, и расписание на Vercel. `budget_s` —
    сколько секунд можно потратить на разбор: у функции Vercel есть предел
    времени, и лучше остановиться и продолжить на следующем проходе, чем
    оборваться посередине.
    """
    out: dict = {}
    try:
        async with SessionLocal() as s:
            stats = await analyze_pending(s, budget_s=budget_s)
        out["analyze"] = stats
        if stats["messages"]:
            log.info("разбор: %s", stats)
    except Exception:
        log.exception("разбор упал")
    try:
        out["tickets"] = await create_tickets_from_chat()
    except Exception:
        log.exception("создание обращений из чата упало")
    try:
        out["replies"] = await detect_replies(self_id)
    except Exception:
        log.exception("поиск ответов клиенту упал")
    try:
        out["diagnosed"] = await auto_diagnose()
    except Exception:
        log.exception("авто-диагноз упал")
    try:
        out["learned"] = await learn_from_closed_tasks()
    except Exception:
        log.exception("обучение из задач упало")
    return out


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    await init_db()
    await start()
    await asyncio.Event().wait()


async def start() -> AsyncIOScheduler:
    """Подключается к Telegram и запускает расписание. Не блокирует."""
    from .ingest.reader import make_client

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
            SELF_ID["id"] = (await client.get_me()).id
            LIVE["client"] = client
    else:
        log.warning(
            "Telethon не настроен или нет сессии — чтение чатов выключено. "
            "Разбор сохранённых сообщений и эскалации работают."
        )
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY не задан — сообщения копятся без разбора")
    if not settings.bot_token:
        log.info("BOT_TOKEN не задан — сводка и эскалации только на сайте")

    tz = ZoneInfo(settings.timezone)
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(
        cycle, "interval", args=[client], minutes=settings.analyze_interval_minutes,
        next_run_time=datetime.now(tz), max_instances=1, coalesce=True,
    )
    if not settings.bot_token:
        # Без бота доставлять некуда: то же самое видно на сайте — «Отчёт»,
        # «Горит прямо сейчас». Расписание отправок просто не ставим.
        sched.start()
        log.info("воркер запущен (без бота: сводка и эскалации — только на сайте)")
        return sched
    sched.add_job(check_escalations, "interval", minutes=5, max_instances=1, coalesce=True)
    sched.add_job(send_morning_digest, "cron", hour=settings.digest_hour_local, minute=0, max_instances=1)
    # Недельный отчёт по сотрудникам: раз в неделю в заданный день и час.
    sched.add_job(
        send_weekly_report,
        "cron",
        day_of_week=settings.weekly_report_weekday,
        hour=settings.weekly_report_hour_local,
        minute=0,
        max_instances=1,
    )
    sched.start()
    log.info("воркер запущен")
    return sched


if __name__ == "__main__":
    asyncio.run(main())
