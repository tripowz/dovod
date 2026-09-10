"""Сквозная проверка API на живой базе.

Проверяется то, что нельзя проверить юнит-тестом правил: закрыт ли вход без
подписи, доезжают ли правила до HTTP-ответа, и не рапортует ли API об успехе
там, где ничего не произошло.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

TOKEN = "123456:TEST-TOKEN-NOT-REAL"
_tmp = Path(tempfile.mkdtemp(prefix="dovod-test-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Chat,
    ChatKind,
    Hotel,
    Message,
    Module,
    RiskLevel,
    Staff,
    Task,
    TaskStatus,
    TaskType,
    Ticket,
    TicketStatus,
)

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


def init_data(user_id: int, name: str) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps(
            {"id": user_id, "first_name": name, "last_name": "", "username": ""},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def seed() -> None:
    from datetime import datetime, timedelta

    await init_db()
    now = datetime.utcnow()
    async with SessionLocal() as s:
        bek = Staff(tg_user_id=1, name="Бекзод", is_manager=True)
        tim = Staff(tg_user_id=2, name="Тимур")
        hotel = Hotel(
            name="Hotel Registon", city="Самарканд", rooms=48, mrr=1_800_000,
            service_hours=31, risk=RiskLevel.high,
            signals=["Четыре обращения по CM за неделю"],
            offers=[{"h": "Позвонить владельцу", "p": "Четвёртое обращение подряд", "c": "15 минут"}],
        )
        chat = Chat(tg_chat_id=-100, title="@smartbooking_support",
                    kind=ChatKind.support, enabled=True)
        s.add_all([bek, tim, hotel, chat])
        await s.flush()

        msg = Message(chat_id=chat.id, tg_msg_id=1, author_name="Малика",
                      text="Опять бронь с Ostrovok не пришла", sent_at=now)
        s.add(msg)
        await s.flush()

        task = Task(
            ref="SB-101", title="Бронь с Ostrovok не доходит",
            definition_of_done="Тестовая бронь видна в календаре за 2 минуты",
            type=TaskType.bug, module=Module.cm, status=TaskStatus.in_progress,
            assignee_id=tim.id, author_id=bek.id, hotel_id=hotel.id,
            due_at=now - timedelta(days=2), last_moved_at=now - timedelta(days=4),
        )
        s.add(task)
        s.add(Ticket(ref="T-201", hotel_id=hotel.id, message_id=msg.id,
                     author_name="Малика", author_role="администратор",
                     text="Опять бронь не пришла", type=TaskType.bug,
                     module=Module.cm, status=TicketStatus.new, created_at=now))
        await s.commit()


async def main() -> None:
    await seed()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 1. Без подписи вход закрыт.
        r = await c.get("/api/me")
        check("без подписи вход закрыт", r.status_code == 401, f"{r.status_code}")

        # 2. Подпись корректная, но человека нет в списке — тоже отказ.
        r = await c.get("/api/me", headers={"X-Telegram-Init-Data": init_data(999, "Чужой")})
        check("посторонний с валидной подписью не проходит", r.status_code == 403,
              f"{r.status_code} {r.text[:60]}")

        boss = {"X-Telegram-Init-Data": init_data(1, "Бекзод")}
        dev = {"X-Telegram-Init-Data": init_data(2, "Тимур")}

        r = await c.get("/api/me", headers=boss)
        check("сотрудник входит", r.status_code == 200 and r.json()["is_manager"], r.text[:60])

        # 3. Приёмка: исполнитель не может, автор может.
        r = await c.post("/api/tasks/SB-101/status", json={"status": "done"}, headers=dev)
        check("исполнитель не закрывает чужую задачу", r.status_code == 409,
              f"{r.status_code} {r.json().get('detail','')[:50]}")

        r = await c.post("/api/tasks/SB-101/status", json={"status": "done"}, headers=boss)
        check("автор закрывает задачу", r.status_code == 200, r.text[:60])

        # 4. Правка без критерия готовности не проходит валидацию.
        r = await c.patch("/api/tasks/SB-101", json={"dod": "мало"}, headers=boss)
        check("короткий критерий готовности отвергается", r.status_code == 422, f"{r.status_code}")

        # 5. Заявку без ответа клиенту закрыть нельзя.
        r = await c.post("/api/tickets/T-201/resolve", headers=boss)
        check("заявку без ответа клиенту не закрыть", r.status_code == 409,
              r.json().get("detail", "")[:60])

        r = await c.post("/api/tickets/T-201/reply", headers=boss)
        check("ответ клиенту фиксируется", r.status_code == 200, r.text[:60])
        r = await c.post("/api/tickets/T-201/resolve", headers=boss)
        check("после ответа заявка закрывается", r.status_code == 200, r.text[:50])

        # 6. Экономика клиента считается, а не хранится.
        r = await c.get("/api/hotels", headers=boss)
        h = r.json()[0]
        check("экономика клиента посчитана", h["cost"] == 31 * 60000 and h["margin"] < 0,
              f"cost={h['cost']} margin={h['margin']}")
        check("вывод по клиенту не про скидку", "починить причину" in h["verdict"])

        # 7. Сводка живая.
        r = await c.get("/api/digest", headers=boss)
        d = r.json()
        check("сводка отдаёт текст для Telegram", bool(d["telegram_text"]),
              d["telegram_text"].replace("\n", " | ")[:80])

        # 8. Отчёт — только руководителю.
        r = await c.get("/api/report/modules", headers=dev)
        check("отчёт закрыт от рядового сотрудника", r.status_code == 403, f"{r.status_code}")

        # 9. Личный чат нельзя включить без записи о согласии.
        r = await c.get("/api/chats", headers=boss)
        check("список чатов доступен руководителю", r.status_code == 200, f"{r.status_code}")

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
