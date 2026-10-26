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
from ..models import FROM_NOW, TELEGRAM_SERVICE_ID, Chat, ChatKind, Message

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
        if e.bot or e.is_self or e.id == TELEGRAM_SERVICE_ID:
            return None  # боты, «Избранное» и служебный чат с кодами входа
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


async def sync_dialogs(
    client: TelegramClient, session: AsyncSession
) -> tuple[int, dict[int, int]]:
    """Регистрирует новые чаты выключенными.

    Возвращает число новых чатов и номер последнего сообщения в каждом чате.
    Список диалогов приходит из Telegram пачками по сотне, поэтому по нему
    дёшево понять, в каких чатах что-то появилось, и не запрашивать историю
    сотен молчащих чатов каждые 15 минут.
    """
    known = {c.tg_chat_id for c in (await session.execute(select(Chat))).scalars()}
    added = 0
    tops: dict[int, int] = {}
    async for d in client.iter_dialogs():
        if d.message is not None:
            tops[d.id] = d.message.id
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
    return added, tops


def _author(m) -> str | None:
    s = m.sender
    if s is None:
        return None
    if isinstance(s, User):
        return " ".join(p for p in (s.first_name, s.last_name) if p) or s.username
    return getattr(s, "title", None)


async def fetch_new(
    client: TelegramClient, session: AsyncSession, tops: dict[int, int] | None = None
) -> int:
    """Забирает новые сообщения из включённых чатов. Возвращает число новых.

    Ограничение: за один проход из чата берётся не больше `backfill_limit`
    сообщений. При интервале в 15 минут на пилоте этого с запасом хватает;
    если чат пишет быстрее, старшая часть окна потеряется — это видно по
    разрыву в tg_msg_id и лечится увеличением лимита.
    `tops` — последние номера сообщений из `sync_dialogs`. Чаты, где нового
    нет, пропускаются без запроса к Telegram. Чат, включённый «с текущего
    момента» (`FROM_NOW`), запоминает свой последний номер и историю не берёт.
    """
    chats = (
        await session.execute(select(Chat).where(Chat.enabled.is_(True)))
    ).scalars().all()
    stored = 0
    tops = tops or {}
    for chat in chats:
        if chat.tg_chat_id == TELEGRAM_SERVICE_ID:
            continue  # коды входа: не читаем, даже если чат включён вручную
        top_now = tops.get(chat.tg_chat_id)
        if chat.last_seen_msg_id == FROM_NOW:
            if top_now is None:
                latest = await client.get_messages(chat.tg_chat_id, limit=1)
                top_now = latest[0].id if latest else 0
            chat.last_seen_msg_id = top_now
            await session.commit()
            continue
        if top_now is not None and top_now <= chat.last_seen_msg_id:
            continue  # в чате ничего нового — не тревожим Telegram
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
