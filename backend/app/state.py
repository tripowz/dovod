"""Ключ-значение в базе (таблица app_state) — память между вызовами на Vercel."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AppState


async def get(session: AsyncSession, key: str, default: str = "") -> str:
    row = await session.get(AppState, key)
    return row.value if row is not None else default


async def put(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppState, key)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row is None:
        session.add(AppState(key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
