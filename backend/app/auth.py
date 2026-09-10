"""Вход в мини-апп: проверка подписи Telegram initData.

Telegram передаёт в WebApp строку `initData` с данными пользователя и
HMAC-подписью. Это единственное доказательство того, кто открыл приложение,
поэтому здесь нет ни одного места, где проверку можно «временно» ослабить:
пропущенная валидация означает, что любой человек с curl представляется
руководителем и закрывает чужие задачи.

Алгоритм (документация Telegram, Mini Apps → Validating data):
1. Из initData убрать `hash`, остальные пары отсортировать по ключу и
   склеить как `k=v` через перевод строки.
2. secret = HMAC-SHA256(ключ="WebAppData", сообщение=bot_token).
3. Ожидаемая подпись = HMAC-SHA256(ключ=secret, сообщение=строка из п.1).

Порядок аргументов в п.2 обратный привычному и на нём чаще всего ошибаются:
токен здесь — сообщение, а не ключ.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import Staff

# Сколько живёт подпись. Сутки — компромисс: Telegram не обновляет initData
# при каждом открытии, а бесконечно валидная подпись превращается в пароль,
# который утёк один раз и работает всегда.
MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str
    last_name: str
    username: str

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or (
            self.username or f"id{self.id}"
        )


def verify_init_data(init_data: str, bot_token: str) -> TelegramUser:
    """Проверяет подпись и возвращает пользователя. Бросает ValueError."""
    if not bot_token:
        # Отдельная ветка: без токена подпись не проверить в принципе, и
        # «пропустить, раз проверить нечем» — ровно та ошибка, из-за которой
        # такие приложения и взламывают.
        raise ValueError("BOT_TOKEN не задан — проверить подпись невозможно")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        raise ValueError("в initData нет подписи")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    # Сравнение с постоянным временем: обычное `==` на строках утекает
    # позицию первого несовпадения и позволяет подобрать подпись побайтово.
    if not hmac.compare_digest(expected, received):
        raise ValueError("подпись не сходится")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise ValueError("некорректный auth_date") from None
    if auth_date <= 0 or time.time() - auth_date > MAX_AUTH_AGE_SECONDS:
        raise ValueError("подпись просрочена, откройте приложение заново")

    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        raise ValueError("не разобрать поле user") from None
    if not user.get("id"):
        raise ValueError("в initData нет пользователя")

    return TelegramUser(
        id=int(user["id"]),
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        username=user.get("username", ""),
    )


async def current_staff(
    authorization: str = Header(default=""),
    x_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
    session: AsyncSession = Depends(get_session),
) -> Staff:
    """Сотрудник, открывший мини-апп.

    Подпись доказывает, что человек — тот, за кого себя выдаёт. Она не
    доказывает, что ему сюда можно: сотрудник должен быть заранее заведён и
    активен. Самозапись запрещена — иначе доступ к задачам и клиентам получает
    любой, кто узнал ссылку на мини-апп.
    """
    raw = x_init_data or (
        authorization[len("tma ") :] if authorization.startswith("tma ") else ""
    )
    if not raw:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Нет данных Telegram. Откройте приложение из чата с ботом.",
        )
    try:
        tg = verify_init_data(raw, settings.bot_token)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None

    staff = (
        await session.execute(select(Staff).where(Staff.tg_user_id == tg.id))
    ).scalar_one_or_none()
    if staff is None or not staff.active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{tg.display_name}, вас нет в списке сотрудников. "
            "Попросите руководителя добавить ваш Telegram.",
        )
    return staff


async def current_manager(staff: Staff = Depends(current_staff)) -> Staff:
    """Руководитель. Отчёт, правила эскалации и настройки источников — сюда."""
    if not staff.is_manager:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Этот раздел доступен только руководителю."
        )
    return staff
