"""Сквозные сценарии: предложение → задача → отмена, удержание, правило закрытия.

Проверяются места, где система раньше врала в прототипе: применение к
исчезнувшей задаче рапортовало об успехе, а кнопки удержания ничего не делали.
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
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

TOKEN = "123456:TEST-TOKEN-NOT-REAL"
_tmp = Path(tempfile.mkdtemp(prefix="dovod-flow-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'f.db').as_posix()}"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Chat, ChatKind, Hotel, Message, Module, Proposal, ProposalKind, ProposalState,
    RiskLevel, Rule, Staff, Task, TaskStatus, TaskType, Ticket, TicketStatus,
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


def headers(user_id: int, name: str) -> dict:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


async def seed() -> None:
    await init_db()
    now = datetime.utcnow()
    async with SessionLocal() as s:
        bek = Staff(tg_user_id=1, name="Бекзод", is_manager=True)
        tim = Staff(tg_user_id=2, name="Тимур")
        hotel = Hotel(name="Hotel Registon", mrr=1_800_000, service_hours=31, risk=RiskLevel.high,
                      offers=[{"h": "Позвонить владельцу сегодня", "p": "…", "c": "15 минут"}])
        chat = Chat(tg_chat_id=-100, title="Разработка", kind=ChatKind.dev, enabled=True)
        s.add_all([bek, tim, hotel, chat])
        await s.flush()

        open_task = Task(ref="SB-101", title="Синк тарифов падает по ночам",
                         definition_of_done="Три ночи логи чистые", type=TaskType.bug,
                         module=Module.cm, status=TaskStatus.in_progress,
                         assignee_id=tim.id, author_id=bek.id, last_moved_at=now)
        closed_task = Task(ref="SB-102", title="Старая задача", definition_of_done="Сделано давно",
                           type=TaskType.request, module=Module.pms, status=TaskStatus.done,
                           assignee_id=tim.id, author_id=bek.id, last_moved_at=now)
        open_task.events = []
        closed_task.events = []
        s.add_all([open_task, closed_task])
        await s.flush()

        texts = ["выкатил, проверьте", "по старой задаче тоже сделал",
                 "у Registon не приходит бронь", "а филиалы как хранить?"]
        msgs = [Message(chat_id=chat.id, tg_msg_id=i, author_name="Тимур", text=t,
                        sent_at=now, analyzed_at=now) for i, t in enumerate(texts, 1)]
        s.add_all(msgs)
        await s.flush()
        s.add_all([
            Proposal(message_id=msgs[0].id, kind=ProposalKind.progress, task_id=open_task.id,
                     link_basis="mention", reading="исполнитель сообщил о выкатке",
                     action="сдать на проверку", confidence=0.9),
            Proposal(message_id=msgs[1].id, kind=ProposalKind.progress, task_id=closed_task.id,
                     link_basis="semantic", reading="…", action="…", confidence=0.7),
            Proposal(message_id=msgs[2].id, kind=ProposalKind.new_task, confidence=0.85,
                     reading="новая поломка", action="завести задачу",
                     draft={"title": "Бронь Ostrovok не доходит до Registon",
                            "dod": "Тестовая бронь видна за 2 минуты", "type": "bug", "module": "CM"}),
            Proposal(message_id=msgs[3].id, kind=ProposalKind.question, task_id=open_task.id,
                     link_basis="reply", reading="вопрос по задаче", action="добавить в нить",
                     confidence=0.8),
        ])
        s.add(Rule(code="close_without_reply", title="Заявка закрыта без ответа", enabled=False))
        s.add_all([
            Ticket(ref="T-301", hotel_id=hotel.id, text="не работает", status=TicketStatus.new, created_at=now),
            Ticket(ref="T-302", hotel_id=hotel.id, text="опять", status=TicketStatus.new, created_at=now),
        ])
        await s.commit()


async def main() -> None:
    await seed()
    boss, dev = headers(1, "Бекзод"), headers(2, "Тимур")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # --- продолжение применяется к живой задаче ---
        r = await c.post("/api/proposals/1/apply", headers=dev)
        check("продолжение применяется", r.status_code == 200 and r.json()["ok"], r.text[:90])
        t = (await c.get("/api/tasks/SB-101", headers=boss)).json()
        check("задача ушла на проверку", t["status"] == "review", t["status"])
        check("сообщение осталось в нити", len(t["thread"]) == 1, str(len(t["thread"])))
        check("ожидание приёмки — последняя запись истории",
              t["events"][-1]["pending"] and "приёмки" in t["events"][-1]["text"], t["events"][-1]["text"])

        r = await c.post("/api/proposals/1/apply", headers=dev)
        check("повторное применение отвергается", r.status_code == 409, f"{r.status_code}")

        # --- продолжение закрытой задачи: честный отказ, не мнимый успех ---
        r = await c.post("/api/proposals/2/apply", headers=dev)
        body = r.json()
        check("к закрытой задаче не применяется и об этом сказано",
              r.status_code == 200 and not body["ok"] and "уже закрыта" in body["message"], body["message"][:70])
        async with SessionLocal() as s:
            m = (await s.execute(select(Message).where(Message.tg_msg_id == 2))).scalar_one()
            check("сообщение вернулось в разбор", m.analyzed_at is None)

        # --- новый запрос: только через форму ---
        r = await c.post("/api/proposals/3/apply", headers=dev)
        check("новый запрос нельзя применить без формы", r.status_code == 409, r.json().get("detail", "")[:50])

        r = await c.get("/api/staff", headers=dev)
        tim_id = next(x["id"] for x in r.json() if x["name"] == "Тимур")
        r = await c.post("/api/tasks", headers=boss, json={
            "title": "Бронь Ostrovok не доходит до Registon", "dod": "Тестовая бронь видна за 2 минуты",
            "type": "bug", "module": "CM", "assignee_id": tim_id,
            "due_at": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            "proposal_id": 3,
        })
        new_ref = r.json().get("ref")
        check("задача из формы создана", r.status_code == 201, r.text[:60])
        inbox = (await c.get("/api/inbox", headers=boss)).json()
        check("оформленный запрос ушёл из входящих", all(p["id"] != 3 for p in inbox["new"]))
        t = (await c.get(f"/api/tasks/{new_ref}", headers=boss)).json()
        check("у новой задачи есть исходное сообщение", len(t["thread"]) == 1)

        # --- удержание клиента: кнопка создаёт задачу ---
        before = (await c.get("/api/hotels", headers=boss)).json()[0]["open_tasks"]
        r = await c.post("/api/hotels/1/offers/0/task", headers=boss)
        check("предложение по удержанию стало задачей", r.status_code == 201, r.text[:70])
        after = (await c.get("/api/hotels", headers=boss)).json()[0]["open_tasks"]
        check("карточка клиента пересчиталась", after == before + 1, f"{before} → {after}")

        # --- отмена задачи возвращает её продолжения в разбор ---
        r = await c.post("/api/tasks/SB-101/cancel", headers=boss)
        check("отменённая задача вернула сообщения в разбор", r.json().get("returned", 0) >= 2, r.text[:80])
        inbox = (await c.get("/api/inbox", headers=boss)).json()
        check("продолжение отменённой задачи не висит во входящих", all(p["id"] != 4 for p in inbox["updates"]))
        async with SessionLocal() as s:
            p4 = (await s.execute(select(Proposal).where(Proposal.id == 4))).scalar_one()
            check("его предложение помечено осиротевшим", p4.state == ProposalState.orphaned)

        # --- правило закрытия без ответа управляет проверкой ---
        r = await c.post("/api/tickets/T-301/resolve", headers=boss)
        check("пока правило выключено, заявка закрывается", r.status_code == 200, f"{r.status_code}")
        r = await c.post("/api/rules/close_without_reply/toggle", headers=boss)
        check("руководитель включает правило", r.json().get("enabled") is True)
        r = await c.post("/api/tickets/T-302/resolve", headers=boss)
        check("с включённым правилом без ответа не закрыть", r.status_code == 409, r.json().get("detail", "")[:50])
        r = await c.post("/api/rules/close_without_reply/toggle", headers=dev)
        check("рядовой сотрудник правила не трогает", r.status_code == 403, f"{r.status_code}")

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
