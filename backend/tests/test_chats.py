"""Источники: массовое включение и чтение «с текущего момента».

Проверяется то, ради чего это сделано: личные переписки массово не
включаются, включённые разом чаты не тянут историю в очередь разбора, а
молчащие чаты не запрашиваются у Telegram каждый проход.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_tmp = Path(tempfile.mkdtemp(prefix="dovod-chats-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'c.db').as_posix()}"
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import chats  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.agents.analyze import analyze_pending, apply_results  # noqa: E402
from app.config import settings  # noqa: E402
from app.ingest.reader import fetch_history, fetch_new  # noqa: E402
from app.models import (  # noqa: E402
    FROM_NOW, TELEGRAM_SERVICE_ID, Chat, ChatKind, Message, Proposal, ProposalKind,
    ProposalState, Staff, Ticket, TicketStatus,
)
from app.ingest.reader import sync_dialogs  # noqa: E402
from app.worker import create_tickets_from_chat, detect_replies  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


class FakeClient:
    """Telegram с фиксированной историей; считает запросы истории."""

    def __init__(self, history: dict[int, list[int]]):
        self.history = history
        self.calls: list[int] = []

    def _msg(self, i: int):
        return SimpleNamespace(
            id=i, action=None, message=f"сообщение {i}", media=None, sender=None,
            sender_id=1, reply_to_msg_id=None, date=datetime(2026, 10, 26, tzinfo=timezone.utc),
        )

    async def get_messages(self, chat_id, limit=1):
        self.calls.append(chat_id)
        ids = sorted(self.history.get(chat_id, []), reverse=True)[:limit]
        return [self._msg(i) for i in ids]

    async def iter_messages(self, chat_id, min_id=0, limit=200):
        self.calls.append(chat_id)
        for i in sorted(self.history.get(chat_id, []), reverse=True)[:limit]:
            if i > min_id:
                yield self._msg(i)


async def main() -> None:
    await init_db()
    async with SessionLocal() as s:
        s.add_all([
            Chat(id=1, tg_chat_id=-101, title="Поддержка", kind=ChatKind.support),
            Chat(id=2, tg_chat_id=-102, title="Команда", kind=ChatKind.team),
            Chat(id=3, tg_chat_id=-103, title="Тихий чат", kind=ChatKind.team),
            Chat(id=4, tg_chat_id=201, title="Азиза", kind=ChatKind.private),
            Chat(id=6, tg_chat_id=TELEGRAM_SERVICE_ID, title="Telegram", kind=ChatKind.private),
        ])
        await s.commit()

    print("=" * 62)
    print("МАССОВОЕ ВКЛЮЧЕНИЕ")
    print("=" * 62)
    code = await chats.main(["on", "all"])
    check("команда завершилась успешно", code == 0)
    async with SessionLocal() as s:
        rows = {c.id: c for c in (await s.execute(select(Chat))).scalars()}
    check("групповые чаты включены", all(rows[i].enabled for i in (1, 2, 3)))
    check("личная переписка не включена", rows[4].enabled is False)
    check("включённые читаются с текущего момента", rows[1].last_seen_msg_id == FROM_NOW)

    print()
    print("=" * 62)
    print("ЧТЕНИЕ")
    print("=" * 62)
    client = FakeClient({-101: [1, 2, 3], -102: [5, 6], -103: [9]})
    tops = {-101: 3, -102: 6, -103: 9}
    async with SessionLocal() as s:
        stored = await fetch_new(client, s, tops)
    check("история при первом проходе не берётся", stored == 0, f"сохранено {stored}")
    check("без запросов истории, если номер известен", client.calls == [], f"{client.calls}")
    async with SessionLocal() as s:
        c1 = await s.get(Chat, 1)
    check("запомнено последнее сообщение чата", c1.last_seen_msg_id == 3, f"{c1.last_seen_msg_id}")

    # Новое сообщение пришло только в поддержку.
    client.history[-101].append(4)
    tops[-101] = 4
    async with SessionLocal() as s:
        stored = await fetch_new(client, s, tops)
        texts = (await s.execute(select(Message.text))).scalars().all()
    check("забрано только новое сообщение", stored == 1 and texts == ["сообщение 4"], f"{texts}")
    check("молчащие чаты не запрашиваются", client.calls == [-101], f"{client.calls}")

    print()
    print("=" * 62)
    print("ВЫКЛЮЧЕНИЕ И ИСТОРИЯ")
    print("=" * 62)
    await chats.main(["off", "all"])
    async with SessionLocal() as s:
        on = [c for c in (await s.execute(select(Chat))).scalars() if c.enabled]
        s.add(Chat(id=5, tg_chat_id=-105, title="Новый", kind=ChatKind.team))
        await s.commit()
    check("все выключены", on == [])
    await chats.main(["on", "all", "--history"])
    async with SessionLocal() as s:
        c5 = await s.get(Chat, 5)
        c1 = await s.get(Chat, 1)
    check("с --history история берётся", c5.enabled and c5.last_seen_msg_id == 0)
    check("повторное включение продолжает с места", c1.last_seen_msg_id == 4, f"{c1.last_seen_msg_id}")

    print()
    print("=" * 62)
    print("ЛИЧНЫЕ ПЕРЕПИСКИ")
    print("=" * 62)
    code = await chats.main(["on", "all", "--private"])
    async with SessionLocal() as s:
        c4 = await s.get(Chat, 4)
    check("без согласия личные не включаются", code == 1 and not c4.enabled)
    code = await chats.main(["on", "all", "--private", "--consent", "служебный", "аккаунт"])
    async with SessionLocal() as s:
        c4 = await s.get(Chat, 4)
        c6 = await s.get(Chat, 6)
    check("с согласием личные включены", code == 0 and c4.enabled)
    check("согласие записано в чат", c4.consent_note == "служебный аккаунт", c4.consent_note)
    check("чат с кодами входа не включён массово", not c6.enabled)
    code = await chats.main(["on", "6", "--consent", "x"])
    async with SessionLocal() as s:
        c6 = await s.get(Chat, 6)
    check("и по одному тоже не включается", code == 1 and not c6.enabled)

    print()
    print("=" * 62)
    print("ИСТОРИЯ ЗА ПЕРИОД")
    print("=" * 62)
    now = datetime.now(timezone.utc)

    class HistoryClient:
        """Чат -201: сообщения 1–3 дня назад и одно 40 дней назад. Чат -202 молчит."""

        def __init__(self):
            self.history_calls: list[int] = []
            self.msgs = {
                -201: [(30, now - timedelta(days=1)), (20, now - timedelta(days=3)),
                       (10, now - timedelta(days=40))],
                -202: [(5, now - timedelta(days=60))],
            }

        async def iter_dialogs(self):
            for cid, ms in self.msgs.items():
                yield SimpleNamespace(id=cid, date=ms[0][1], message=SimpleNamespace(id=ms[0][0]))

        async def iter_messages(self, chat_id, limit=2000):
            self.history_calls.append(chat_id)
            for i, d in self.msgs[chat_id]:
                yield SimpleNamespace(
                    id=i, action=None, message=f"история {i}", media=None, sender=None,
                    sender_id=1, reply_to_msg_id=None, date=d,
                )

    async with SessionLocal() as s:
        s.add_all([
            Chat(id=21, tg_chat_id=-201, title="Отель", kind=ChatKind.support, enabled=True,
                 last_seen_msg_id=FROM_NOW),
            Chat(id=22, tg_chat_id=-202, title="Молчит", kind=ChatKind.team, enabled=True),
        ])
        await s.commit()
    hc = HistoryClient()
    async with SessionLocal() as s:
        read, stored = await fetch_history(hc, s, days=30)
        texts = sorted(
            (await s.execute(select(Message.text).where(Message.chat_id == 21))).scalars()
        )
        c21 = await s.get(Chat, 21)
    check("загружены сообщения только за период", texts == ["история 20", "история 30"], f"{texts}")
    check("молчащий чат не запрашивается", hc.history_calls == [-201], f"{hc.history_calls}")
    check("курсор чата «с текущего момента» встал на последнее", c21.last_seen_msg_id == 30)
    async with SessionLocal() as s:
        _, again = await fetch_history(hc, s, days=30)
    check("повторная загрузка не дублирует", again == 0, f"{again}")

    print()
    print("=" * 62)
    print("СВЕЖЕЕ ВПЕРЁД ИСТОРИИ, ЗАЯВКИ ТОЛЬКО ИЗ СВЕЖЕГО")
    print("=" * 62)
    naive = now.replace(tzinfo=None)
    async with SessionLocal() as s:
        old = [
            Message(chat_id=21, tg_msg_id=100 + i, text="ок", sent_at=naive - timedelta(days=5, minutes=i))
            for i in range(12)
        ]
        fresh = Message(chat_id=21, tg_msg_id=200, text="ок", sent_at=naive - timedelta(minutes=5))
        s.add_all(old + [fresh])
        await s.commit()
        fresh_id = fresh.id
    saved = settings.analyze_batch_size
    settings.analyze_batch_size = 1  # за проход — 10 сообщений, меньше, чем ждёт
    try:
        async with SessionLocal() as s:
            await s.execute(Message.__table__.update().where(Message.chat_id == 21, Message.tg_msg_id < 100).values(analyzed_at=naive))
            await s.commit()
            await analyze_pending(s)
            f = await s.get(Message, fresh_id)
        check("свежее разобрано при очереди из истории", f.analyzed_at is not None)
    finally:
        settings.analyze_batch_size = saved

    async with SessionLocal() as s:
        m_old = Message(chat_id=21, tg_msg_id=300, text="бронь не пришла", sent_at=naive - timedelta(days=3))
        m_new = Message(chat_id=21, tg_msg_id=301, text="бронь не пришла", sent_at=naive - timedelta(minutes=3))
        s.add_all([m_old, m_new])
        await s.flush()
        s.add_all([
            Proposal(message_id=m_old.id, kind=ProposalKind.new_task, confidence=0.9,
                     draft={"title": "Бронь", "type": "bug", "module": "CM"}),
            Proposal(message_id=m_new.id, kind=ProposalKind.new_task, confidence=0.9,
                     draft={"title": "Бронь", "type": "bug", "module": "CM"}),
        ])
        await s.commit()
        old_id, new_id = m_old.id, m_new.id
    await create_tickets_from_chat()
    async with SessionLocal() as s:
        from_msgs = set((await s.execute(select(Ticket.message_id))).scalars())
    check("из старого сообщения заявка не создана", old_id not in from_msgs)
    check("из свежего — создана", new_id in from_msgs, f"{from_msgs}")

    print()
    print("=" * 62)
    print("ИСТОРИЯ НЕ ИДЁТ ВО «ВХОДЯЩИЕ»")
    print("=" * 62)
    async with SessionLocal() as s:
        h_old = Message(chat_id=21, tg_msg_id=400, text="добавьте выгрузку", sent_at=naive - timedelta(days=10))
        h_new = Message(chat_id=21, tg_msg_id=401, text="добавьте выгрузку", sent_at=naive - timedelta(hours=1))
        s.add_all([h_old, h_new])
        await s.flush()
        res = [
            {"message_id": h_old.id, "kind": "new_task", "confidence": 0.9, "draft": {"title": "Выгрузка"}},
            {"message_id": h_new.id, "kind": "new_task", "confidence": 0.9, "draft": {"title": "Выгрузка"}},
        ]
        await apply_results(s, [h_old, h_new], res, {}, "test", naive)
        await s.commit()
        states = {
            p.message_id: p.state
            for p in (await s.execute(select(Proposal).where(Proposal.message_id.in_([h_old.id, h_new.id])))).scalars()
        }
    check("старое — в архив истории", states.get(h_old.id) == ProposalState.history, f"{states}")
    check("свежее — во «Входящие»", states.get(h_new.id) == ProposalState.pending, f"{states}")

    print()
    print("=" * 62)
    print("ОТВЕТ КЛИЕНТУ ПРЯМО В TELEGRAM")
    print("=" * 62)
    async with SessionLocal() as s:
        s.add(Staff(id=70, tg_user_id=7000, name="Азиза"))
        q = Message(chat_id=21, tg_msg_id=500, tg_user_id=111, text="не пришла бронь", sent_at=naive - timedelta(minutes=20))
        a = Message(chat_id=21, tg_msg_id=501, tg_user_id=7000, text="смотрю", sent_at=naive - timedelta(minutes=12))
        q2 = Message(chat_id=21, tg_msg_id=502, tg_user_id=111, text="и ещё вопрос", sent_at=naive - timedelta(minutes=5))
        s.add_all([q, a, q2])
        await s.flush()
        s.add_all([
            Ticket(ref="T-900", message_id=q.id, text=q.text, status=TicketStatus.new, created_at=q.sent_at),
            Ticket(ref="T-901", message_id=q2.id, text=q2.text, status=TicketStatus.new, created_at=q2.sent_at),
        ])
        await s.commit()
    await detect_replies(self_id=None)
    async with SessionLocal() as s:
        t900 = (await s.execute(select(Ticket).where(Ticket.ref == "T-900"))).scalar_one()
        t901 = (await s.execute(select(Ticket).where(Ticket.ref == "T-901"))).scalar_one()
    check("ответ сотрудника в чате снял ожидание", t900.first_reply_at is not None)
    check("ответивший стал исполнителем", t900.assignee_id == 70, f"{t900.assignee_id}")
    check("заявка после ответа всё ещё ждёт", t901.first_reply_at is None)
    async with SessionLocal() as s:
        s.add(Message(chat_id=21, tg_msg_id=503, tg_user_id=9999, text="ответ с аккаунта", sent_at=naive - timedelta(minutes=1)))
        await s.commit()
    await detect_replies(self_id=9999)
    async with SessionLocal() as s:
        t901 = (await s.execute(select(Ticket).where(Ticket.ref == "T-901"))).scalar_one()
    check("ответ со служебного аккаунта тоже считается", t901.first_reply_at is not None)

    print()
    print("=" * 62)
    print("ЛИЧКА КАК ПОДДЕРЖКА, ТИП ЧАТА, НОВЫЕ ЧАТЫ")
    print("=" * 62)
    async with SessionLocal() as s:
        s.add(Chat(id=31, tg_chat_id=301, title="Отель в личке", kind=ChatKind.private, enabled=True))
        pm = Message(chat_id=31, tg_msg_id=1, text="не работает касса", sent_at=naive - timedelta(minutes=2))
        s.add(pm)
        await s.flush()
        s.add(Proposal(message_id=pm.id, kind=ProposalKind.new_task, confidence=0.9, draft={"type": "bug"}))
        await s.commit()
        pm_id = pm.id
    await create_tickets_from_chat()
    async with SessionLocal() as s:
        has = pm_id in set((await s.execute(select(Ticket.message_id))).scalars())
    check("по умолчанию из лички заявки нет", not has)
    settings.private_as_support = True
    try:
        await create_tickets_from_chat()
    finally:
        settings.private_as_support = False
    async with SessionLocal() as s:
        has = pm_id in set((await s.execute(select(Ticket.message_id))).scalars())
    check("с PRIVATE_AS_SUPPORT из лички заявка есть", has)

    code = await chats.main(["kind", "support", "2", "31"])
    async with SessionLocal() as s:
        c2, c31 = await s.get(Chat, 2), await s.get(Chat, 31)
    check("kind меняет тип группы", c2.kind == ChatKind.support)
    check("тип личной переписки не меняется", c31.kind == ChatKind.private and code == 1)

    class Dialogs:
        def __init__(self, ds):
            self.ds = ds

        async def iter_dialogs(self):
            for d in self.ds:
                yield d

    def dlg(i, name):
        from telethon.tl.types import Chat as TgChat
        e = TgChat.__new__(TgChat)
        return SimpleNamespace(id=i, name=name, entity=e, message=None)

    settings.new_chats_consent = "служебный аккаунт"
    try:
        async with SessionLocal() as s:
            await sync_dialogs(Dialogs([dlg(-777, "Новый отель & Smartbooking")]), s)
            fresh_chat = (await s.execute(select(Chat).where(Chat.tg_chat_id == -777))).scalar_one()
    finally:
        settings.new_chats_consent = ""
    check("новый чат включён сам", fresh_chat.enabled and fresh_chat.last_seen_msg_id == FROM_NOW)
    check("с записью о согласии", fresh_chat.consent_note == "служебный аккаунт")
    async with SessionLocal() as s:
        await sync_dialogs(Dialogs([dlg(-778, "Ещё чат")]), s)
        off_chat = (await s.execute(select(Chat).where(Chat.tg_chat_id == -778))).scalar_one()
    check("без NEW_CHATS_CONSENT новый чат выключен", not off_chat.enabled)

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
