"""Тип группового чата по названию — общая догадка для Telethon и бота."""

from __future__ import annotations

from ..models import ChatKind


def kind_by_title(title: str) -> ChatKind:
    """Грубая догадка; руководитель поправит на сайте («Команда» → «Чаты»)."""
    t = title.lower()
    if "support" in t or "поддерж" in t:
        return ChatKind.support
    if "разраб" in t or "dev" in t:
        return ChatKind.dev
    if "продаж" in t or "sales" in t:
        return ChatKind.sales
    return ChatKind.team
