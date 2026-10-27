"""Приём сообщений от Telegram через webhook — без своего сервера.

Telegram сам присылает каждое обновление на адрес на Vercel:

- `business_message` — личная переписка служебного аккаунта (Telegram
  Business: владелец подключил бота в настройках аккаунта). Пишут и клиенты,
  и сам владелец — его сообщения потом считаются ответами клиенту;
- `message` из группы — бот добавлен в рабочую группу (privacy mode
  выключен, иначе бот видит только команды);
- `business_connection` — владелец подключил или отключил бота;
- `my_chat_member` — бота добавили в группу или убрали из неё;
- `message` в личке самого бота — команды /start, /id, вход кодом; это
  обрабатывает модуль бота, а не этот.

Бот только читает: прав отвечать от имени аккаунта ему не нужно, и
клиентам он ничего не пишет.

Здесь только сохранение — быстро, чтобы Telegram получил ответ сразу.
Разбор моделью делает проход по расписанию (`/api/cron/tick`).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import state
from ..config import settings
from ..models import TELEGRAM_SERVICE_ID, Chat, ChatKind, Message
from .kinds import kind_by_title

log = logging.getLogger("dovod.webhook")

BUSINESS_USER = "business_user_id"
BUSINESS_ENABLED = "business_enabled"

CONSENT_BUSINESS = "Telegram Business: переписку подключил владелец аккаунта"
CONSENT_GROUP = "бот добавлен в группу её участниками"

ALLOWED_UPDATES = ["message", "business_message", "business_connection", "my_chat_member"]

_MEDIA = ("photo", "document", "video", "voice", "audio", "video_note", "sticker", "animation")


def webhook_secret() -> str:
    """Секрет в заголовке каждого запроса от Telegram.

    Без него любой мог бы прислать на адрес поддельное «сообщение клиента».
    Задаётся явно (WEBHOOK_SECRET) или выводится из токена бота — тогда
    сервер и команда настройки webhook получают одно и то же значение.
    """
    if settings.webhook_secret:
        return settings.webhook_secret
    return hashlib.sha256(b"dovod-webhook:" + settings.bot_token.encode()).hexdigest()[:48]


def _author(user: dict | None) -> str | None:
    if not user:
        return None
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
    return name or user.get("username")


def _title(chat: dict) -> str:
    if chat.get("title"):
        return str(chat["title"])
    name = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
    return name or chat.get("username") or f"чат {chat.get('id')}"


async def _chat(
    session: AsyncSession, tg: dict, kind: ChatKind, consent: str
) -> Chat | None:
    """Чат из базы или новый — включённый: его подключили к боту намеренно."""
    tg_id = int(tg["id"])
    if tg_id == TELEGRAM_SERVICE_ID:
        return None  # служебный чат Telegram с кодами входа — никогда
    chat = (await session.execute(select(Chat).where(Chat.tg_chat_id == tg_id))).scalar_one_or_none()
    if chat is None:
        chat = Chat(
            tg_chat_id=tg_id,
            title=_title(tg)[:255],
            kind=kind,
            enabled=True,
            consent_note=consent,
        )
        session.add(chat)
        await session.flush()
        log.info("новый чат: %s (%s)", chat.title, kind.value)
    return chat


async def _store(session: AsyncSession, chat: Chat, msg: dict) -> bool:
    """Сохраняет сообщение. False — нечего сохранять или уже есть."""
    # Выключенный руководителем чат не читаем, даже если бот в нём остался.
    if not chat.enabled:
        return False
    text = msg.get("text") or msg.get("caption") or ""
    media = next((k for k in _MEDIA if k in msg), None)
    if not text and not media:
        return False  # служебное: «вступил в группу», «закрепил»
    msg_id = int(msg["message_id"])
    dup = (
        await session.execute(
            select(Message.id).where(Message.chat_id == chat.id, Message.tg_msg_id == msg_id)
        )
    ).first()
    if dup is not None:
        return False  # Telegram повторил доставку
    sender = msg.get("from") or {}
    reply = msg.get("reply_to_message") or {}
    session.add(
        Message(
            chat_id=chat.id,
            tg_msg_id=msg_id,
            tg_user_id=sender.get("id"),
            author_name=_author(sender),
            text=text,
            reply_to_msg_id=reply.get("message_id"),
            media_kind=media,
            sent_at=datetime.fromtimestamp(int(msg["date"]), timezone.utc).replace(tzinfo=None),
        )
    )
    if msg_id > chat.last_seen_msg_id:
        chat.last_seen_msg_id = msg_id
    return True


def is_bot_private(update: dict) -> bool:
    """Сообщение в личку самому боту: /start, /id, вход кодом."""
    msg = update.get("message") or {}
    return (msg.get("chat") or {}).get("type") == "private"


async def ingest(session: AsyncSession, update: dict) -> str:
    """Сохраняет одно обновление Telegram. Возвращает, что с ним сделано."""
    if conn := update.get("business_connection"):
        user = conn.get("user") or {}
        await state.put(session, BUSINESS_USER, str(user.get("id", "")))
        await state.put(session, BUSINESS_ENABLED, "1" if conn.get("is_enabled") else "0")
        await session.commit()
        return "business_connection"

    if msg := update.get("business_message"):
        chat = await _chat(session, msg["chat"], ChatKind.private, CONSENT_BUSINESS)
        stored = chat is not None and await _store(session, chat, msg)
        await session.commit()
        return "stored" if stored else "skipped"

    if member := update.get("my_chat_member"):
        tg = member.get("chat") or {}
        status = (member.get("new_chat_member") or {}).get("status")
        if tg.get("type") in ("group", "supergroup") and status in ("member", "administrator"):
            await _chat(session, tg, kind_by_title(_title(tg)), CONSENT_GROUP)
            await session.commit()
            return "group_added"
        return "skipped"

    msg = update.get("message")
    if msg and (msg.get("chat") or {}).get("type") in ("group", "supergroup"):
        tg = msg["chat"]
        chat = await _chat(session, tg, kind_by_title(_title(tg)), CONSENT_GROUP)
        stored = chat is not None and await _store(session, chat, msg)
        await session.commit()
        return "stored" if stored else "skipped"

    return "skipped"


async def owner_id(session: AsyncSession) -> int | None:
    """Telegram ID владельца Business-аккаунта: его сообщения — ответы клиенту."""
    raw = await state.get(session, BUSINESS_USER)
    return int(raw) if raw.lstrip("-").isdigit() else None
