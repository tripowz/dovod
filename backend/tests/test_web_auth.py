"""Вход на сайт через Telegram: ссылка в бота → код → cookie.

Проверяется то, на чём такие входы обычно ломаются: код нельзя подобрать,
чужая ссылка на вход не даёт войти под сотрудником, сессию нельзя подделать,
а запрос с чужого сайта по cookie не проходит.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

TOKEN = "123456:TEST-TOKEN-NOT-REAL"
_tmp = Path(tempfile.mkdtemp(prefix="dovod-web-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'w.db').as_posix()}"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app import auth  # noqa: E402
from app.api import web_auth  # noqa: E402
from app.bot import telegram as bot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import LoginRequest, Staff  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


sent: list[tuple[int, str]] = []


async def fake_call(method: str, **kw):
    if method == "getMe":
        return {"username": "sbsupportoffice_bot"}
    return True


async def fake_send(chat_id: int, text: str, *, with_app: bool = False) -> None:
    sent.append((chat_id, text))


bot.call = fake_call
bot.send = fake_send


async def tg_start(user_id: int, payload: str) -> str:
    """Сообщение в бота от пользователя; возвращает ответ бота."""
    sent.clear()
    await bot.handle_update({"message": {
        "chat": {"id": user_id, "type": "private"}, "from": {"id": user_id}, "text": f"/start {payload}",
    }})
    return sent[-1][1] if sent else ""


def code_of(reply: str) -> str:
    return "".join(ch for ch in reply.split("\n")[0] if ch.isdigit())


H = {"X-Dovod": "1"}


async def main() -> None:
    await init_db()
    async with SessionLocal() as s:
        s.add_all([
            Staff(id=1, tg_user_id=555, name="Бекзод", is_manager=True),
            Staff(id=2, tg_user_id=777, name="Азиза"),
            Staff(id=3, tg_user_id=999, name="Уволен", active=False),
        ])
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        print("=" * 62)
        print("ВХОД")
        print("=" * 62)
        r = await c.get("/api/me")
        check("без входа — 401", r.status_code == 401)
        r = await c.post("/api/auth/start")
        check("старт без заголовка сайта отклонён", r.status_code == 403)
        r = await c.post("/api/auth/start", headers=H)
        start = r.json()
        check("ссылка ведёт в бота", start["url"].startswith("https://t.me/sbsupportoffice_bot?start=login_"))
        payload = start["url"].split("start=")[1]
        check("ключ влезает в /start", len(payload) <= 64, f"{len(payload)}")

        r = await c.post("/api/auth/verify", json={"nonce": start["nonce"], "code": "000000"}, headers=H)
        check("до бота код не принимается", r.status_code == 409)

        reply = await tg_start(555, payload)
        code = code_of(reply)
        check("бот прислал шестизначный код", len(code) == 6, reply[:40])

        r = await c.post("/api/auth/verify", json={"nonce": start["nonce"], "code": "123456" if code != "123456" else "654321"}, headers=H)
        check("неверный код — 401 и остаток попыток", r.status_code == 401 and "Осталось попыток: 4" in r.text)
        r = await c.post("/api/auth/verify", json={"nonce": start["nonce"], "code": f"{code[:3]} {code[3:]}"}, headers=H)
        check("верный код (с пробелом) — вход", r.status_code == 200 and r.json()["name"] == "Бекзод", r.text[:80])
        cookie = r.headers.get("set-cookie", "")
        check("cookie только для сервера", "HttpOnly" in cookie and "SameSite=lax" in cookie, cookie[:120])

        r = await c.get("/api/me")
        check("сессия работает", r.status_code == 200 and r.json()["is_manager"] is True)
        r = await c.get("/api/inbox")
        check("данные доступны по cookie", r.status_code == 200)
        r = await c.post("/api/tickets/T-1/resolve")
        check("изменение без заголовка сайта — 403 (CSRF)", r.status_code == 403, r.text[:60])

        r = await c.post("/api/auth/verify", json={"nonce": start["nonce"], "code": code}, headers=H)
        check("код одноразовый", r.status_code == 410)

        await c.post("/api/auth/logout", headers=H)
        c.cookies.clear()
        r = await c.get("/api/me")
        check("после выхода — 401", r.status_code == 401)

        print()
        print("=" * 62)
        print("АТАКИ")
        print("=" * 62)
        s2 = (await c.post("/api/auth/start", headers=H)).json()
        p2 = s2["url"].split("start=")[1]
        for i in range(5):
            await c.post("/api/auth/verify", json={"nonce": s2["nonce"], "code": f"{i:06d}"}, headers=H)
        await tg_start(777, p2)  # код пришёл — но подбор уже исчерпан?
        for i in range(5):
            await c.post("/api/auth/verify", json={"nonce": s2["nonce"], "code": f"{i:06d}"}, headers=H)
        r = await c.post("/api/auth/verify", json={"nonce": s2["nonce"], "code": "999999"}, headers=H)
        check("после 5 неверных кодов — блок", r.status_code == 429, f"{r.status_code}")

        # Злоумышленник начал вход и прислал ссылку Азизе. Код приходит Азизе,
        # у злоумышленника есть только ключ — войти нельзя.
        s3 = (await c.post("/api/auth/start", headers=H)).json()
        reply = await tg_start(777, s3["url"].split("start=")[1])
        check("код ушёл владельцу аккаунта", sent and sent[-1][0] == 777 and code_of(reply))
        r = await c.post("/api/auth/verify", json={"nonce": s3["nonce"], "code": "000001"}, headers=H)
        check("с одним ключом без кода не войти", r.status_code == 401)

        reply = await tg_start(12345, (await c.post("/api/auth/start", headers=H)).json()["url"].split("start=")[1])
        check("не сотруднику код не приходит", "нет в списке" in reply and not code_of(reply.split("ID")[0]), reply[:60])
        reply = await tg_start(999, (await c.post("/api/auth/start", headers=H)).json()["url"].split("start=")[1])
        check("отключённому — тоже", "нет в списке" in reply)

        reply = await tg_start(555, "login_выдуманный")
        check("выдуманная ссылка — «устарела»", "устарела" in reply)

        s4 = (await c.post("/api/auth/start", headers=H)).json()
        async with SessionLocal() as s:
            from sqlalchemy import select
            req = (await s.execute(select(LoginRequest).order_by(LoginRequest.id.desc()))).scalars().first()
            req.created_at -= timedelta(minutes=11)
            await s.commit()
        reply = await tg_start(555, s4["url"].split("start=")[1])
        check("старая ссылка — «устарела»", "устарела" in reply)

        forged = auth.make_session(1).split(".")[0] + ".00"
        c.cookies.set(auth.SESSION_COOKIE, forged)
        r = await c.get("/api/me")
        check("подделанная сессия — 401", r.status_code == 401)
        c.cookies.set(auth.SESSION_COOKIE, auth.make_session(1, now=0))
        r = await c.get("/api/me")
        check("просроченная сессия — 401", r.status_code == 401 and "истекла" in r.text)
        c.cookies.set(auth.SESSION_COOKIE, auth.make_session(3))
        r = await c.get("/api/me")
        check("сессия отключённого — 403", r.status_code == 403)
        c.cookies.clear()

    print()
    print("=" * 62)
    print("ВХОД ПО ЛОГИНУ И ПАРОЛЮ (БЕЗ БОТА)")
    print("=" * 62)
    from app import passwords
    from app import run as run_mod
    from app.api import web_auth as wa

    async with SessionLocal() as s:
        boss = await s.get(Staff, 1)
        boss.login = "bekzod"
        boss.password_hash = passwords.hash_password("k7mq-x2vd-p9wn")
        await s.commit()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/auth/methods")
        check("способы входа видны", r.json() == {"password": True, "telegram": True})
        r = await c.post("/api/auth/login", json={"login": "bekzod", "password": "k7mq-x2vd-p9wn"})
        check("вход без заголовка сайта отклонён", r.status_code == 403)
        r = await c.post("/api/auth/login", json={"login": "bekzod", "password": "нет"}, headers=H)
        r2 = await c.post("/api/auth/login", json={"login": "nobody", "password": "нет"}, headers=H)
        check("неверный пароль и чужой логин — одна ошибка", r.status_code == r2.status_code == 401 and r.text == r2.text, r.text)
        r = await c.post("/api/auth/login", json={"login": " Bekzod ", "password": "k7mq-x2vd-p9wn"}, headers=H)
        check("вход по паролю (регистр логина не важен)", r.status_code == 200 and r.json()["name"] == "Бекзод", r.text[:80])
        check("после входа — сессия", (await c.get("/api/me")).status_code == 200)

        r = await c.post("/api/staff", json={"name": "Малика Каримова"}, headers=H)
        new = r.json()
        check("сотрудник без Telegram ID заводится", r.status_code == 201 and new["tg_linked"] is False, r.text[:120])
        check("логин из имени", new["login"] == "malika.karimova", new.get("login"))
        check("пароль показан один раз", len(new.get("password", "")) == 14)
        r = await c.post("/api/staff", json={"name": "Малика Каримова"}, headers=H)
        check("тёзка получает свободный логин", r.json()["login"] == "malika.karimova2", r.json().get("login"))
        r = await c.get("/api/staff/manage")
        check("в списке пароля нет", all("password" not in x for x in r.json()) and all("password_hash" not in x for x in r.json()))
        r = await c.post(f"/api/staff/{new['id']}/password", headers=H)
        fresh = r.json()["password"]
        check("новый пароль выдаётся", fresh and fresh != new["password"])

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c2:
        r = await c2.post("/api/auth/login", json={"login": "malika.karimova", "password": new["password"]}, headers=H)
        check("старый пароль после сброса не работает", r.status_code == 401)
        r = await c2.post("/api/auth/login", json={"login": "malika.karimova", "password": fresh}, headers=H)
        check("новый пароль работает", r.status_code == 200)
        r = await c2.get("/api/staff/manage")
        check("сотрудник не видит управление командой", r.status_code == 403)

        wa._FAILS.clear()
        for _ in range(10):
            await c2.post("/api/auth/login", json={"login": "bekzod", "password": "x"}, headers=H)
        r = await c2.post("/api/auth/login", json={"login": "bekzod", "password": "k7mq-x2vd-p9wn"}, headers=H)
        check("после 10 ошибок логин заблокирован, даже верным паролем", r.status_code == 429)
        wa._FAILS.clear()

    print()
    print("=" * 62)
    print("ПЕРВЫЙ ЗАПУСК БЕЗ БОТА")
    print("=" * 62)
    from app.config import settings
    from sqlalchemy import delete

    async with SessionLocal() as s:
        await s.execute(delete(Staff))
        await s.commit()
    saved = (settings.manager_tg_user_id, settings.manager_name)
    settings.manager_tg_user_id, settings.manager_name = 0, "Бекзод"
    try:
        boss, pw = await run_mod.ensure_manager()
        check("руководитель создан без Telegram ID", boss is not None and boss.is_manager and boss.tg_user_id < 0)
        check("логин и пароль выданы", boss.login == "bekzod" and len(pw) == 14, f"{boss.login} {pw}")
        boss2, pw2 = await run_mod.ensure_manager()
        check("повторный запуск пароль не меняет", boss2.id == boss.id and pw2 == "")
    finally:
        settings.manager_tg_user_id, settings.manager_name = saved

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
