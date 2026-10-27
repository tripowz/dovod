"""Вход на сайт: по логину и паролю, а если подключён бот — ещё и кодом из Telegram.

Код через бота:

1. Сайт: POST /api/auth/start — получает ссылку в бота с одноразовым ключом.
2. Человек открывает ссылку, бот видит его Telegram ID (это подтверждает сам
   Telegram) и присылает шестизначный код — только сотруднику из списка.
3. Сайт: POST /api/auth/verify с ключом и кодом — получает cookie-сессию.

Код, а не «подтвердите кнопкой в боте», — намеренно. При подтверждении
кнопкой злоумышленник мог бы прислать сотруднику свою ссылку на вход и после
нажатия оказаться в системе под ним. Код приходит в Telegram владельцу, и
чужая ссылка злоумышленнику ничего не даёт.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CSRF_HEADER, SESSION_COOKIE, SESSION_DAYS, make_session
from ..config import settings
from ..db import get_session
from ..models import LoginRequest, Staff
from ..passwords import check_password

router = APIRouter(prefix="/api/auth")

LINK_TTL = timedelta(minutes=10)  # сколько живёт ссылка в бота
CODE_TTL = timedelta(minutes=5)  # сколько живёт присланный код
MAX_ATTEMPTS = 5
START_PREFIX = "login_"

_bot_username: dict[str, str] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def bot_username() -> str:
    if settings.bot_username:
        return settings.bot_username.lstrip("@")
    if "u" not in _bot_username:
        from ..bot.telegram import call  # бот импортирует модели, а не наоборот

        me = await call("getMe")
        _bot_username["u"] = me["username"]  # type: ignore[index]
    return _bot_username["u"]


@router.post("/start")
async def start(request: Request, session: AsyncSession = Depends(get_session)):
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Запрос не с сайта Довода.")
    now = _now()
    await session.execute(delete(LoginRequest).where(LoginRequest.created_at < now - LINK_TTL))
    pending = (
        await session.execute(
            select(func.count(LoginRequest.id)).where(LoginRequest.used_at.is_(None))
        )
    ).scalar() or 0
    # Потолок незавершённых входов: иначе таблицу можно засыпать запросами.
    if pending > 500:
        raise HTTPException(429, "Слишком много попыток входа. Подождите несколько минут.")
    try:
        name = await bot_username()
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Бот недоступен: проверьте BOT_TOKEN.") from None

    nonce = secrets.token_urlsafe(24)  # 32 символа: в /start влезает до 64
    session.add(LoginRequest(nonce_hash=_hash(nonce), created_at=now))
    await session.commit()
    return {
        "nonce": nonce,
        "url": f"https://t.me/{name}?start={START_PREFIX}{nonce}",
        "bot": name,
        "expires_in": int(LINK_TTL.total_seconds()),
    }


async def issue_code(session: AsyncSession, nonce: str, staff: Staff) -> str | None:
    """Вызывается ботом: создаёт код для входа. None — ссылка устарела."""
    req = (
        await session.execute(select(LoginRequest).where(LoginRequest.nonce_hash == _hash(nonce)))
    ).scalar_one_or_none()
    now = _now()
    if req is None or req.used_at is not None or now - req.created_at > LINK_TTL:
        return None
    code = f"{secrets.randbelow(10**6):06d}"
    req.code_hash = _hash(f"{nonce}:{code}")
    req.staff_id = staff.id
    req.code_sent_at = now
    req.attempts = 0
    await session.commit()
    return code


class VerifyIn(BaseModel):
    nonce: str
    code: str


@router.post("/verify")
async def verify(
    body: VerifyIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Запрос не с сайта Довода.")
    req = (
        await session.execute(
            select(LoginRequest).where(LoginRequest.nonce_hash == _hash(body.nonce))
        )
    ).scalar_one_or_none()
    now = _now()
    if req is None or req.used_at is not None or now - req.created_at > LINK_TTL:
        raise HTTPException(410, "Ссылка для входа устарела. Нажмите «Войти» ещё раз.")
    if not req.code_hash or req.code_sent_at is None:
        raise HTTPException(409, "Сначала откройте бота по ссылке и нажмите «Запустить» — он пришлёт код.")
    if now - req.code_sent_at > CODE_TTL:
        raise HTTPException(410, "Код устарел. Откройте бота по ссылке ещё раз — придёт новый.")
    if req.attempts >= MAX_ATTEMPTS:
        raise HTTPException(429, "Слишком много неверных кодов. Нажмите «Войти» ещё раз.")

    code = "".join(ch for ch in body.code if ch.isdigit())
    if not hmac.compare_digest(req.code_hash, _hash(f"{body.nonce}:{code}")):
        req.attempts += 1
        await session.commit()
        left = MAX_ATTEMPTS - req.attempts
        raise HTTPException(401, f"Код не подходит. Осталось попыток: {left}.")

    staff = await session.get(Staff, req.staff_id) if req.staff_id else None
    if staff is None or not staff.active:
        raise HTTPException(403, "Вас нет в списке сотрудников. Попросите руководителя добавить вас.")
    req.used_at = now
    await session.commit()

    _set_session(request, response, staff)
    return {"id": staff.id, "name": staff.name, "is_manager": staff.is_manager}


# Подбор пароля: не больше 10 неудачных попыток на логин за 15 минут.
# В памяти процесса — сайт и API живут в одном процессе.
_FAILS: dict[str, list[datetime]] = {}
FAIL_WINDOW = timedelta(minutes=15)
MAX_FAILS = 10


class LoginIn(BaseModel):
    login: str
    password: str


def _set_session(request: Request, response: Response, staff: Staff) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        make_session(staff.id),
        max_age=SESSION_DAYS * 86400,
        httponly=True,  # скрипт на странице сессию не прочитает
        samesite="lax",
        secure=secure,
        path="/",
    )


@router.get("/methods")
async def methods():
    """Какие способы входа доступны: код через бота — только если бот есть."""
    return {"password": True, "telegram": bool(settings.bot_token)}


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Запрос не с сайта Довода.")
    name = body.login.strip().lower()
    now = _now()
    fails = [t for t in _FAILS.get(name, []) if now - t < FAIL_WINDOW]
    _FAILS[name] = fails
    if len(fails) >= MAX_FAILS:
        raise HTTPException(429, "Слишком много неверных попыток. Подождите 15 минут.")

    staff = (
        await session.execute(select(Staff).where(Staff.login == name))
    ).scalar_one_or_none()
    # Одна и та же ошибка для «нет логина» и «не тот пароль»: иначе по ответу
    # можно перебрать, какие логины существуют.
    if staff is None or not check_password(body.password, staff.password_hash):
        fails.append(now)
        raise HTTPException(401, "Неверный логин или пароль.")
    if not staff.active:
        raise HTTPException(403, "Ваш доступ отключён. Обратитесь к руководителю.")
    _FAILS.pop(name, None)
    _set_session(request, response, staff)
    return {"id": staff.id, "name": staff.name, "is_manager": staff.is_manager}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
