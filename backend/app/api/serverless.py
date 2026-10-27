"""Адреса для работы без своего сервера (Vercel + Supabase).

- POST /api/telegram/webhook — сюда Telegram присылает каждое сообщение.
- POST /api/cron/tick — сюда раз в минуту стучится расписание Supabase
  (pg_cron + pg_net): проход агентов, эскалации, сводка в 09:00.

Оба адреса открыты в интернет, поэтому каждый проверяет свой секрет.
"""

from __future__ import annotations

import hmac
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Request

from .. import state
from ..config import settings
from ..db import SessionLocal
from ..ingest import webhook

router = APIRouter(prefix="/api")
log = logging.getLogger("dovod.serverless")

LOCK = "tick_lock"


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    secret: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
):
    if not settings.bot_token or not hmac.compare_digest(secret, webhook.webhook_secret()):
        raise HTTPException(401, "неверный секрет webhook")
    update = await request.json()
    if webhook.is_bot_private(update):
        from ..bot.telegram import handle_update  # /start, /id, вход кодом

        await handle_update(update)
        return {"ok": True, "result": "bot"}
    # Ошибка базы — 500: Telegram повторит доставку, а дубли отсекаются.
    async with SessionLocal() as s:
        result = await webhook.ingest(s, update)
    return {"ok": True, "result": result}


def _check_cron(authorization: str) -> None:
    if not settings.cron_secret:
        raise HTTPException(503, "CRON_SECRET не задан — проход по расписанию выключен")
    if not hmac.compare_digest(authorization, f"Bearer {settings.cron_secret}"):
        raise HTTPException(401, "неверный секрет расписания")


async def _take_lock(now: datetime) -> bool:
    """Один проход за раз: расписание стучится раз в минуту, а проход может
    идти дольше. Два прохода параллельно разбирали бы одни и те же сообщения
    и платили модели дважды."""
    async with SessionLocal() as s:
        held = await state.get(s, LOCK)
        if held:
            try:
                since = datetime.fromisoformat(held)
            except ValueError:
                since = now - timedelta(days=1)
            if now - since < timedelta(seconds=settings.tick_budget_seconds + 120):
                return False
        await state.put(s, LOCK, now.isoformat())
        await s.commit()
    return True


async def _release_lock() -> None:
    async with SessionLocal() as s:
        await state.put(s, LOCK, "")
        await s.commit()


async def _once_a_day(key: str, today: str) -> bool:
    """True — если сегодня ещё не делали; сразу отмечает сделанным."""
    async with SessionLocal() as s:
        if await state.get(s, key) == today:
            return False
        await state.put(s, key, today)
        await s.commit()
    return True


@router.post("/cron/tick")
@router.get("/cron/tick")
async def cron_tick(authorization: str = Header(default="")):
    _check_cron(authorization)
    from .. import worker

    started = time.monotonic()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not await _take_lock(now):
        return {"ok": True, "skipped": "прошлый проход ещё идёт"}
    out: dict = {}
    try:
        async with SessionLocal() as s:
            owner = await webhook.owner_id(s)
        out = await worker.run_agents(owner, budget_s=settings.tick_budget_seconds)

        if settings.bot_token:
            try:
                out["escalations"] = await worker.check_escalations()
            except Exception:
                log.exception("эскалации упали")

            local = datetime.now(ZoneInfo(settings.timezone))
            today = local.date().isoformat()
            if local.hour >= settings.digest_hour_local and await _once_a_day("digest_sent", today):
                from ..bot.telegram import send_morning_digest

                out["digest"] = await send_morning_digest()
            if (
                local.weekday() == settings.weekly_report_weekday
                and local.hour >= settings.weekly_report_hour_local
                and await _once_a_day("weekly_sent", today)
            ):
                from ..bot.telegram import send_weekly_report

                out["weekly"] = await send_weekly_report()
    finally:
        await _release_lock()
    out["seconds"] = round(time.monotonic() - started, 1)
    return {"ok": True, **out}
