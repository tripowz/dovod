"""Чаты → отели → карточки клиентов.

Проверяется на настоящих названиях чатов пилота: группа «X & Smartbooking» и
личка «X. Имя» сходятся в один отель, люди и внутренние чаты отелем не
становятся, таблица для Excel читается обратно, а карточка считает переписку.
"""

from __future__ import annotations

import asyncio
import csv
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dovod-hotels-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'h.db').as_posix()}"
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import hotels, service  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Chat, ChatKind, Hotel, HotelContact, Message, Proposal, ProposalKind, ProposalState, Staff,
    Ticket,
)
from app.worker import create_tickets_from_chat  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


CHATS = [
    # (№, tg id, название, тип)
    (1, -1001, "Diplomat Premium & Smartbooking", ChatKind.team),
    (2, 501, "Diplomat Premium. Nosir aka", ChatKind.private),
    (3, -1003, "Medinatown & Smartbooking", ChatKind.team),
    (4, 504, "Abduvohid. Medina Town", ChatKind.private),
    (5, 505, "Medina Town. Шерзод", ChatKind.private),
    (6, -1006, "Vella Elegant & Smartbooking", ChatKind.team),
    (7, 507, "Голибжон менеджер Велла Элегант", ChatKind.private),
    (8, 508, "Iskandar Hotel. Aziz aka", ChatKind.private),
    (9, 509, "Искандар отель. Синдор. Админ", ChatKind.private),
    (10, 510, "AHMAD HOTEL SAMARKAND", ChatKind.private),
    (11, -1011, "Рабочая группа SmartBooking", ChatKind.team),
    (12, -1012, "SMART TEST", ChatKind.team),
    (13, 513, "Abbos Ismoil", ChatKind.private),
    (14, 514, "Farangizz…💘", ChatKind.private),
    (15, 515, "Арда Плаза. Фирдавс", ChatKind.private),
    (16, 516, "Arda Plaza Hotel", ChatKind.private),
]


async def main() -> None:
    await init_db()
    async with SessionLocal() as s:
        for i, tg, title, kind in CHATS:
            s.add(Chat(id=i, tg_chat_id=tg, title=title, kind=kind, enabled=True))
        await s.commit()

    print("=" * 62)
    print("УГАДЫВАНИЕ")
    print("=" * 62)
    async with SessionLocal() as s:
        sug = {c.id: (h, contact) for c, h, contact in await hotels._suggestions(s)}
    k = lambda i: hotels.key(sug[i][0])  # noqa: E731
    check("группа и личка — один отель", k(1) == k(2) == "diplomatpremium", f"{sug[1]} {sug[2]}")
    check("контакт из лички", sug[2][1] == "Nosir aka", sug[2][1])
    check("«Abduvohid. Medina Town» — это Medinatown", k(4) == k(3) == k(5), f"{sug[4]}")
    check("имя отеля — как в группе", sug[4][0] == "Medinatown", sug[4][0])
    check("отель узнан внутри названия", sug[7][0] == "Vella Elegant", f"{sug[7]}")
    check("латиница и кириллица сходятся", k(8) == k(9) == "iskandar", f"{sug[8]} {sug[9]}")
    check("«Арда Плаза» = «Arda Plaza Hotel»", k(15) == k(16), f"{sug[15]} {sug[16]}")
    check("город — не контакт", sug[10][1] == "", f"{sug[10]}")
    check("внутренние чаты — не отели", not sug[11][0] and not sug[12][0])
    check("люди без признаков — не отели", not sug[13][0] and not sug[14][0])

    print()
    print("=" * 62)
    print("ТАБЛИЦА ДЛЯ EXCEL")
    print("=" * 62)
    path = _tmp / "hotels.csv"
    await hotels.cmd_suggest(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    check("в таблице все чаты", len(rows) == len(CHATS), f"{len(rows)}")
    # Человек правит таблицу: Farangizz — тоже из Diplomat, «Abbos Ismoil» не трогает.
    for r in rows:
        if r["№"] == "14":
            r["отель"] = "Diplomat Premium"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    await hotels.cmd_apply(path)
    async with SessionLocal() as s:
        chats = {c.id: c for c in (await s.execute(select(Chat))).scalars()}
        hs = {h.id: h for h in (await s.execute(select(Hotel))).scalars()}
        contacts = (await s.execute(select(HotelContact))).scalars().all()
    names = sorted(h.name for h in hs.values())
    check("отели заведены без дублей", len(names) == 6, f"{names}")
    check("правка из таблицы применилась", chats[14].hotel_id == chats[1].hotel_id)
    check("внутренний чат без отеля", chats[11].hotel_id is None)
    nosir = [c for c in contacts if c.tg_user_id == 501]
    check("собеседник из лички стал контактом", nosir and nosir[0].name == "Nosir aka", f"{[c.name for c in contacts]}")

    await hotels.cmd_apply(path)
    async with SessionLocal() as s:
        again = (await s.execute(select(Hotel))).scalars().all()
    check("повторная загрузка не плодит отели", len(again) == 6, f"{len(again)}")

    print()
    print("=" * 62)
    print("КАРТОЧКА КЛИЕНТА ИЗ ПЕРЕПИСКИ")
    print("=" * 62)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dip = chats[1].hotel_id
    async with SessionLocal() as s:
        msgs = []
        for i in range(6):
            m = Message(chat_id=1 if i % 2 else 2, tg_msg_id=100 + i, tg_user_id=501,
                        text=f"не работает {i}", sent_at=now - timedelta(days=i * 3))
            s.add(m)
            msgs.append(m)
        old = Message(chat_id=1, tg_msg_id=99, text="давно", sent_at=now - timedelta(days=45))
        s.add(old)
        await s.flush()
        for i, m in enumerate(msgs):
            s.add(Proposal(
                message_id=m.id, kind=ProposalKind.new_task, confidence=0.9,
                state=ProposalState.history if i else ProposalState.pending,
                draft={"type": "bug", "module": "CM"},
            ))
        await s.commit()
        act = await hotels.activity(s, now)
    a = act[dip]
    check("сообщения за 30 дней, без старых", a.messages == 6, f"{a.messages}")
    check("обращения считаются и из истории", a.requests == 6 and a.by_type == {"bug": 6})
    check("все чаты отеля в карточке", len(a.chats) == 3, f"{a.chats}")
    sig = service.activity_signals(a, now, 30)
    check("сигнал об обращениях", sig and sig[0].startswith("За 30 дн. 6 обращений: 6 багов"), f"{sig}")
    check("сигнал о модуле", any("Чаще всего про CM: 6 из 6" in x for x in sig), f"{sig}")
    check("6 багов за месяц — высокий риск", service.live_risk("low", a, 0, now) == "high")

    medina = chats[3].hotel_id
    async with SessionLocal() as s:
        s.add(Message(chat_id=3, tg_msg_id=1, text="спасибо", sent_at=now - timedelta(days=25)))
        await s.commit()
        a2 = (await hotels.activity(s, now))[medina]
    check("молчание — тоже сигнал", any("Молчит 25 дн." in x for x in service.activity_signals(a2, now, 30)))
    check("долгое молчание поднимает риск", service.live_risk("low", a2, 0, now) == "medium")
    check("ручной высокий риск не снижается", service.live_risk("high", a2, 0, now) == "high")

    async with SessionLocal() as s:
        boss = Staff(tg_user_id=1, name="Бекзод", is_manager=True)
        s.add(boss)
        await s.commit()
        hl = (await s.execute(select(Hotel))).scalars().all()
        act = await hotels.activity(s, now)
    d = service.build_digest([], [], list(hl), boss, now, act)
    check("сводка видит отель по переписке", any(x["name"] == "Diplomat Premium" for x in d.at_risk),
          f"{[x['name'] for x in d.at_risk]}")
    econ = service.hotel_economics(hs[dip])
    check("без тарифа экономика не выдумывается", "не указаны" in econ.verdict, econ.verdict)

    print()
    print("=" * 62)
    print("ЗАЯВКА ПОЛУЧАЕТ ОТЕЛЬ ПО ЧАТУ")
    print("=" * 62)
    async with SessionLocal() as s:
        c6 = await s.get(Chat, 6)
        c6.kind = ChatKind.support
        m = Message(chat_id=6, tg_msg_id=1, tg_user_id=777, text="касса не печатает чек",
                    sent_at=now - timedelta(minutes=3))
        s.add(m)
        await s.flush()
        s.add(Proposal(message_id=m.id, kind=ProposalKind.new_task, confidence=0.9, draft={"type": "bug"}))
        await s.commit()
        mid = m.id
    await create_tickets_from_chat()
    async with SessionLocal() as s:
        t = (await s.execute(select(Ticket).where(Ticket.message_id == mid))).scalar_one()
        vella = (await s.get(Chat, 6)).hotel_id
    check("незнакомый автор — отель по чату", t.hotel_id == vella and vella is not None)

    print()
    print("=" * 62)
    print("РУЧНЫЕ КОМАНДЫ")
    print("=" * 62)
    await hotels.main(["set", "Diplomat Premium", "mrr=1 800 000", "city=Самарканд", "rooms=48"])
    async with SessionLocal() as s:
        h = await s.get(Hotel, dip)
    check("set заполняет карточку", h.mrr == 1_800_000 and h.city == "Самарканд" and h.rooms == 48)
    await hotels.main(["link", "13", "Arda Plaza"])
    await hotels.main(["unlink", "11"])
    async with SessionLocal() as s:
        c13 = await s.get(Chat, 13)
        arda = (await s.get(Chat, 16)).hotel_id
    check("link к существующему отелю по ключу", c13.hotel_id == arda)

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
