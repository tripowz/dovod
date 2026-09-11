"""Чтение чатов через Telethon (user-API).

Только чтение. Клиент поднимается с защитой: пока включён
`telethon_read_only`, методы, оставляющие след у других людей, заменены
исключением. Это не формальность — сессия принадлежит живому сотруднику, и
любое сообщение, ушедшее через неё, выглядело бы как написанное им лично.

Новые чаты регистрируются выключенными. Чтение переписки не должно
начинаться само оттого, что сотрудника добавили в новую группу, — включает
его руководитель, а личную переписку — только с записью о согласии.
"""

from __future__ import annotations

import logging
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.tl.types import Channel, User

from ..config import settings
from ..models import Chat, ChatKind, Message

log = logging.getLogger("dovod.reader")

# Всё, что видно другим людям. send_read_acknowledge тоже здесь: отметка
# «прочитано» видна собеседнику, и система, читающая переписку, не должна
# выдавать себя галочками.
BLOCKED = (
    "send_message",
    "send_file",
    "forward_messages",
    "edit_message",
    "delete_messages",
    "send_read_acknowledge",
    "pin_message",
    "unpin_message",
    "kick_participant",
    "edit_admin",
    "edit_permissions",
)


def make_client() -> TelegramClient:
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        raise RuntimeError(
            "TELEGRAM_API_ID и TELEGRAM_API_HASH не заданы "
            "(my.telegram.org → API development tools)"
        )
    client = TelegramClient(
        settings.telegram_session_path, settings.telegram_api_id, settings.telegram_api_hash
    )
    if settings.telethon_read_only:
        for name in BLOCKED:

            def _blocked(*_a, _name: str = name, **_k):
                raise RuntimeError(
                    f"Telethon работает только на чтение: {_name} запрещён. "
                    "Исходящее идёт через бота."
                )

            setattr(client, name, _blocked)
    return client


def classify(dialog) -> ChatKind | None:
    """Грубая догадка о типе чата по названию. Руководитель поправит в
    «Источниках»; важно лишь не пропустить личные переписки и каналы."""
    e = dialog.entity
    if isinstance(e, User):
        if e.bot or e.is_self:
            return None
        return ChatKind.private
    if isinstance(e, Channel) and e.broadcast:
        return None  # каналы — объявления, а не переписка
    t = (dialog.name or "").lower()
    if "support" in t or "поддерж" in t:
        return ChatKind.support
    if "разраб" in t or "dev" in t:
        return ChatKind.dev
    if "продаж" in t or "sales" in t:
        return ChatKind.sales
    return ChatKind.team


async def sync_dialogs(client: TelegramClient, session: AsyncSession) -> int:
    """Регистрирует новые чаты выключенными. Возвращает число новых."""
    known = {c.tg_chat_id for c in (await session.execute(select(Chat))).scalars()}
    added = 0
    async for d in client.iter_dialogs():
        kind = classify(d)
        if kind is None or d.id in known:
            continue
        session.add(
            Chat(
                tg_chat_id=d.id,
                title=(d.name or f"чат {d.id}")[:255],
                kind=kind,
                enabled=False,
            )
        )
        added += 1
    await session.commit()
    return added


def _author(m) -> str | None:
    s = m.sender
    if s is None:
        return None
    if isinstance(s, User):
        return " ".join(p for p in (s.first_name, s.last_name) if p) or s.username
    return getattr(s, "title", None)


async def fetch_new(client: TelegramClient, session: AsyncSession) -> int:
    """Забирает новые сообщения из включённых чатов. Возвращает число новых.

    Ограничение: за один проход из чата берётся не больше `backfill_limit`
    сообщений. При интервале в 15 минут на пилоте этого с запасом хватает;
    если чат пишет быстрее, старшая часть окна потеряется — это видно по
    разрыву в tg_msg_id и лечится увеличением лимита.
    """
    chats = (
        await session.execute(select(Chat).where(Chat.enabled.is_(True)))
    ).scalars().all()
    stored = 0
    for chat in chats:
        existing = set(
            (
                await session.execute(
                    select(Message.tg_msg_id).where(
                        Message.chat_id == chat.id,
                        Message.tg_msg_id > chat.last_seen_msg_id,
                    )
                )
            ).scalars()
        )
        top = chat.last_seen_msg_id
        try:
            async for m in client.iter_messages(
                chat.tg_chat_id, min_id=chat.last_seen_msg_id, limit=settings.backfill_limit
            ):
                top = max(top, m.id)
                if m.action is not None or m.id in existing:
                    continue  # служебные: «вступил в группу», «закрепил»
                text = m.message or ""
                media = type(m.media).__name__ if m.media else None
                if not text and not media:
                    continue
                session.add(
                    Message(
                        chat_id=chat.id,
                        tg_msg_id=m.id,
                        tg_user_id=m.sender_id,
                        author_name=_author(m),
                        text=text,
                        reply_to_msg_id=m.reply_to_msg_id,
                        media_kind=media,
                        sent_at=m.date.astimezone(timezone.utc).replace(tzinfo=None),
                    )
                )
                stored += 1
        except Exception as exc:
            # Один недоступный чат (вышли из группы, отобрали права) не должен
            # останавливать чтение остальных.
            log.warning("чат «%s» не прочитан: %s", chat.title, exc)
            await session.rollback()
            continue
        chat.last_seen_msg_id = top
        await session.commit()
    return stored
