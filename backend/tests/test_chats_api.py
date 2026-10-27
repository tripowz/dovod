"""Экран «Чаты» на сайте: что видит Telethon и что из этого читается.

Правила те же, что у команды `app.chats`: личную переписку без основания не
включить, служебный чат с кодами входа — не включить никак, включённый чат
читается с этого момента. Плюс то, что есть только у сайта: «Обновить
сейчас» и загрузка истории через Telethon в этом же процессе.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dovod-chatsapi-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'c.db').as_posix()}"
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")
os.environ["SESSION_SECRET_PATH"] = str(_tmp / "web.secret")
os.environ["BOT_TOKEN"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app import auth, worker  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import FROM_NOW, TELEGRAM_SERVICE_ID, Chat, ChatKind, Hotel, Message, Staff  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


H = {"X-Dovod": "1"}


async def main() -> None:
    await init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with SessionLocal() as s:
        s.add_all([
            Staff(id=1, tg_user_id=-1, name="Бекзод", is_manager=True),
            Staff(id=2, tg_user_id=-2, name="Азиза"),
            Chat(id=1, tg_chat_id=-101, title="Diplomat Premium & Smartbooking", kind=ChatKind.team),
            Chat(id=2, tg_chat_id=502, title="Medina Town. Шерзод", kind=ChatKind.private),
            Chat(id=3, tg_chat_id=TELEGRAM_SERVICE_ID, title="Telegram", kind=ChatKind.private),
            Chat(id=4, tg_chat_id=-104, title="Рабочая группа", kind=ChatKind.team, enabled=True,
                 last_seen_msg_id=50),
        ])
        await s.flush()
        s.add(Message(chat_id=4, tg_msg_id=50, text="привет", sent_at=now - timedelta(days=2)))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.cookies.set(auth.SESSION_COOKIE, auth.make_session(2))
        r = await c.get("/api/chats/full")
        check("сотруднику экран чатов закрыт", r.status_code == 403)
        c.cookies.set(auth.SESSION_COOKIE, auth.make_session(1))

        r = await c.get("/api/chats/full")
        d = r.json()
        by = {x["id"]: x for x in d["chats"]}
        check("видны все чаты", len(d["chats"]) == 4)
        check("счётчик и дата последнего сообщения", by[4]["messages"] == 1 and by[4]["last_message_at"])
        check("без Telethon в процессе — live выключен", d["live"] is False)

        r = await c.patch("/api/chats/1", json={"enabled": True})
        check("изменение без заголовка сайта — 403", r.status_code == 403)
        r = await c.patch("/api/chats/1", json={"enabled": True, "kind": "support", "hotel": "Diplomat Premium"}, headers=H)
        check("группа включена, тип и отель заданы", r.status_code == 200 and r.json()["hotel"] == "Diplomat Premium", r.text)
        async with SessionLocal() as s:
            c1 = await s.get(Chat, 1)
            hotel = (await s.get(Hotel, c1.hotel_id)).name if c1.hotel_id else ""
        check("включённый читается с этого момента", c1.enabled and c1.last_seen_msg_id == FROM_NOW)
        check("отель создан", hotel == "Diplomat Premium")

        r = await c.patch("/api/chats/2", json={"enabled": True}, headers=H)
        check("личная без основания не включается", r.status_code == 409, r.text[:80])
        r = await c.patch("/api/chats/2", json={"enabled": True, "consent_note": "служебный аккаунт"}, headers=H)
        check("личная с основанием включается", r.status_code == 200 and r.json()["enabled"])
        r = await c.patch("/api/chats/2", json={"kind": "support"}, headers=H)
        check("личную нельзя сделать группой", r.status_code == 409)
        r = await c.patch("/api/chats/3", json={"enabled": True, "consent_note": "x"}, headers=H)
        check("чат с кодами входа не включается", r.status_code == 409)
        r = await c.patch("/api/chats/1", json={"hotel": ""}, headers=H)
        check("отвязка от отеля", r.json()["hotel"] == "")

        r = await c.post("/api/chats/enable-all", json={"private": True}, headers=H)
        check("массово личные — только с основанием", r.status_code == 409)
        await c.patch("/api/chats/1", json={"enabled": False}, headers=H)
        r = await c.post("/api/chats/enable-all", json={}, headers=H)
        async with SessionLocal() as s:
            c3 = await s.get(Chat, 3)
        check("«включить все группы» — без чата с кодами", r.json()["enabled"] == 1 and not c3.enabled, r.text)

        r = await c.post("/api/sync", headers=H)
        check("«Обновить» без Telethon — понятный отказ", r.status_code == 409 and "app.run" in r.text)

        # Telethon в этом же процессе: «Обновить сейчас» запускает проход воркера.
        ran: list[object] = []

        async def fake_cycle(client) -> None:
            ran.append(client)

        real_cycle = worker.cycle
        worker.cycle = fake_cycle
        worker.LIVE["client"] = "telethon"
        try:
            r = await c.get("/api/chats/full")
            check("с Telethon — live", r.json()["live"] is True)
            r = await c.post("/api/sync", headers=H)
            await asyncio.sleep(0.05)
            check("«Обновить сейчас» запускает проход", r.status_code == 200 and ran == ["telethon"], f"{ran}")
        finally:
            worker.cycle = real_cycle
            worker.LIVE.clear()

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
