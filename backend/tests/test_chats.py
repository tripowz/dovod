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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

_tmp = Path(tempfile.mkdtemp(prefix="dovod-chats-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'c.db').as_posix()}"
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import chats  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.ingest.reader import fetch_new  # noqa: E402
from app.models import FROM_NOW, TELEGRAM_SERVICE_ID, Chat, ChatKind, Message  # noqa: E402

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
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
