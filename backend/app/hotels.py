"""Связь чатов с отелями: из переписки наполняются карточки клиентов.

    python -m app.hotels                         — отели и сколько у них чатов
    python -m app.hotels suggest [файл.csv]      — предложить отель каждому чату
    python -m app.hotels apply [файл.csv]        — применить (после правки в Excel)
    python -m app.hotels auto                    — предложить и сразу применить
    python -m app.hotels link 15 "Diplomat Premium"
    python -m app.hotels unlink 15
    python -m app.hotels set "Diplomat Premium" mrr=1800000 city=Самарканд rooms=48 tariff="PMS + CM"

Отель угадывается по названию чата: «Diplomat Premium & Smartbooking» и
«Diplomat Premium. Nosir aka» — один отель, Nosir — его контакт. Угадывание
грубое, поэтому основной путь — `suggest`: таблица открывается в Excel,
ошибки правятся руками, `apply` загружает результат. Пустой отель в строке —
«это не отель» (внутренний чат, частное лицо).
"""

from __future__ import annotations

import asyncio
import csv
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import service
from .config import settings
from .db import SessionLocal, init_db
from .models import Chat, ChatKind, Hotel, HotelContact, Message, Proposal, ProposalKind

DEFAULT_FILE = "hotels.csv"

# --------------------------------------------------------------------------
# Угадывание отеля по названию чата
# --------------------------------------------------------------------------

_CYR = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sh", "", "y", "", "e", "yu", "ya",
     "o", "q", "g", "h"],
))

# Слова, которые не отличают один отель от другого: «Iskandar Hotel» и
# «Искандар отель» — один и тот же клиент.
_GENERIC = {
    "hotel", "otel", "hotell", "mehmonhona", "gostinitsa", "hostel", "hostell", "hostella",
    "the", "and", "i", "x",
}

# Города: «Noble Samarqand» и «Noble» — один клиент, город названия не отличает.
_CITIES = {
    "samarkand", "samarqand", "samarqan", "bukhara", "buxara", "buhara", "khiva", "xiva", "hiva",
    "tashkent", "toshkent", "tashkand", "namangan", "andijan", "andijon", "fergana", "ferghana",
    "fargona", "qarshi", "karshi", "nukus", "urgench", "termez", "navoi", "navoiy", "kokand",
    "qoqon", "zaamin", "zomin", "jizzakh", "uzbekistan",
}

# Признаки того, что в названии есть отель, а не просто имя человека.
_MARKERS = re.compile(
    r"(hotel|отель|hostel|хостел|guest ?house|гест ?хаус|гостевой|plaza|плаза|palace|палас|"
    r"resort|ресорт|boutique|бутик|\binn\b|инн|sanator|санатор|mehmon|мехмон|гостиниц|"
    r"apart|апарт|karvon|карвон|caravan|караван|madrasa|мадраса)",
    re.I,
)

# Хвосты, которые описывают человека или должность, а не отель.
_ROLE_TAIL = re.compile(
    r"\s*[\.,|/-]?\s*\b(reception|reseption|resepcion|resepshin|ресепшн|ресепш|респшн|admin|"
    r"administrator|administration|админ|manager|menedjer|менеджер|director|direktor|директор|"
    r"бухгалтер|sales manager)\b.*$",
    re.I,
)

# Чат с отелем в названии несёт имя компании: «X & Smartbooking», «X x SmartBooking».
_SB_GROUP = re.compile(r"^(.*?)\s*(?:&|\bx\b|×|\bи\b)\s*smart\s*booking\b.*$", re.I)


def key(name: str) -> str:
    """Ключ для сравнения названий: латиница, без регистра и общих слов."""
    s = "".join(_CYR.get(ch, ch) for ch in name.lower())
    tokens = re.findall(r"[a-z0-9]+", s)
    # Без пробелов: «Medinatown» и «Medina Town», «GrandAsia» и «Grand Asia».
    return "".join(t for t in tokens if t not in _GENERIC and t not in _CITIES)


def _is_city(word: str) -> bool:
    return key(word) == "" and bool(word.strip())


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip(" .,-|/:;\"'«»")).strip()


def _known_in(t: str, known: frozenset[str]) -> str:
    """Самый длинный известный отель, чьё название есть в тексте."""
    k = key(t)
    hits = [kn for kn in known if len(kn) >= 6 and kn in k]
    return max(hits, key=len) if hits else ""


def _contact(s: str) -> str:
    """Контакт без города и должности: «SAMARKAND» — не человек."""
    words = [w for w in _clean(_ROLE_TAIL.sub("", s)).split() if not _is_city(w)]
    return " ".join(words)


def suggest(title: str, kind: ChatKind, known: frozenset[str] = frozenset()) -> tuple[str, str]:
    """(отель, контакт) по названию чата. Пустой отель — это не отель.

    `known` — ключи уже известных отелей: с ними узнаётся и «Abduvohid.
    Medina Town», где признака отеля в названии нет.
    """
    t = _clean(title)
    if not t:
        return "", ""
    if kind != ChatKind.private:
        m = _SB_GROUP.match(t)
        return (_clean(m.group(1)), "") if m and _clean(m.group(1)) else ("", "")

    # Личка: «Отель. Человек», «Отель / Человек», «Человек | Отель» — разделитель
    # есть, и отелем считаем ту часть, где есть признак отеля (или левую).
    parts = [p for p in (_clean(x) for x in re.split(r"\s*(?:\.\s|\s/\s|\s\|\s)\s*", t)) if p]
    if len(parts) >= 2:
        hotel_i = next(
            (i for i, p in enumerate(parts) if key(p) and key(p) in known),
            next((i for i, p in enumerate(parts) if _MARKERS.search(p) and key(p)), 0),
        )
        hotel = _clean(_ROLE_TAIL.sub("", parts[hotel_i]))
        contact = " ".join(p for i, p in enumerate(parts) if i != hotel_i)
        return (hotel, _contact(contact)) if key(hotel) else ("", "")

    # Без разделителя отель угадываем только по явному признаку в названии.
    t = _clean(_ROLE_TAIL.sub("", t))
    if key(t) in known:
        return t, ""
    if not _MARKERS.search(t):
        # «Old Station Комил Ака», «Голибжон менеджер Велла Элегант» — отель
        # узнаётся, только если он уже известен по другим чатам.
        if hit := _known_in(title, known):
            return KNOWN_NAMES.get(hit, ""), ""
        return "", ""
    words = t.split()
    marks = [i for i, w in enumerate(words) if _MARKERS.search(w)]
    last = marks[-1]
    if last == 0 and len(words) > 1:
        # «Hotel Dera», «Отель Мирон»: признак впереди, имя — следующее слово.
        return " ".join(words[:2]), _contact(" ".join(words[2:]))
    # «Al Arda Hotel Шахбоз»: всё до признака — отель, после — человек.
    return " ".join(words[: last + 1]), _contact(" ".join(words[last + 1 :]))


# Ключ → отображаемое имя для `_known_in`; заполняется в `_suggestions`.
KNOWN_NAMES: dict[str, str] = {}


# --------------------------------------------------------------------------
# Активность отеля по переписке
# --------------------------------------------------------------------------


async def activity(
    session: AsyncSession, now: datetime, days: int = 30
) -> dict[int, service.HotelActivity]:
    """Сводка переписки по каждому отелю, у которого есть связанные чаты."""
    since = now - timedelta(days=days)
    chats = (
        await session.execute(select(Chat).where(Chat.hotel_id.is_not(None)))
    ).scalars().all()
    out: dict[int, service.HotelActivity] = {}
    by_chat: dict[int, int] = {}
    for c in chats:
        out.setdefault(c.hotel_id, service.HotelActivity()).chats.append(c.title)
        by_chat[c.id] = c.hotel_id
    if not by_chat:
        return out

    for chat_id, n, last in (
        await session.execute(
            select(Message.chat_id, func.count(Message.id), func.max(Message.sent_at))
            .where(Message.chat_id.in_(by_chat))
            .group_by(Message.chat_id)
        )
    ).all():
        a = out[by_chat[chat_id]]
        if last is not None and (a.last_message_at is None or last > a.last_message_at):
            a.last_message_at = last
    for chat_id, n in (
        await session.execute(
            select(Message.chat_id, func.count(Message.id))
            .where(Message.chat_id.in_(by_chat), Message.sent_at >= since)
            .group_by(Message.chat_id)
        )
    ).all():
        out[by_chat[chat_id]].messages += n

    # Запросы — то, что разбор счёл новым запросом, в каком бы состоянии
    # предложение ни было: оформленное, отклонённое или из истории.
    for chat_id, draft in (
        await session.execute(
            select(Message.chat_id, Proposal.draft)
            .join(Proposal, Proposal.message_id == Message.id)
            .where(
                Message.chat_id.in_(by_chat),
                Message.sent_at >= since,
                Proposal.kind == ProposalKind.new_task,
                # Порог тот же, что у «Входящих»: неуверенная догадка бота
                # не должна поднимать клиенту риск.
                Proposal.confidence >= settings.proposal_min_confidence,
            )
        )
    ).all():
        a = out[by_chat[chat_id]]
        a.requests += 1
        d = draft or {}
        if t := d.get("type"):
            a.by_type[t] = a.by_type.get(t, 0) + 1
        if m := d.get("module"):
            a.by_module[m] = a.by_module.get(m, 0) + 1
    return out


# --------------------------------------------------------------------------
# Привязка
# --------------------------------------------------------------------------


async def _hotel_for(session: AsyncSession, name: str, cache: dict[str, Hotel]) -> Hotel:
    """Отель с таким названием (по ключу) или новый."""
    k = key(name)
    if not cache:
        for h in (await session.execute(select(Hotel))).scalars():
            cache.setdefault(key(h.name), h)
    h = cache.get(k)
    if h is None:
        h = Hotel(name=name[:255], signals=[], offers=[])
        session.add(h)
        await session.flush()
        cache[k] = h
    return h


async def link(
    session: AsyncSession, chat: Chat, hotel_name: str, contact: str, cache: dict[str, Hotel]
) -> Hotel | None:
    """Привязывает чат к отелю; для лички заводит контакт отеля."""
    if not hotel_name.strip():
        chat.hotel_id = None
        return None
    h = await _hotel_for(session, hotel_name.strip(), cache)
    chat.hotel_id = h.id
    # Собеседник в личке — человек из отеля: его сообщения в общих чатах
    # тоже относятся к этому отелю, а заявки получают автора.
    if chat.kind == ChatKind.private and chat.tg_chat_id > 0:
        have = (
            await session.execute(
                select(HotelContact).where(HotelContact.tg_user_id == chat.tg_chat_id)
            )
        ).scalar_one_or_none()
        if have is None:
            session.add(
                HotelContact(hotel_id=h.id, tg_user_id=chat.tg_chat_id, name=(contact or chat.title)[:255])
            )
        else:
            have.hotel_id = h.id
    return h


async def _suggestions(session: AsyncSession) -> list[tuple[Chat, str, str]]:
    chats = (
        await session.execute(select(Chat).options().order_by(Chat.title))
    ).scalars().all()
    hotels = {h.id: h for h in (await session.execute(select(Hotel))).scalars()}
    # Два прохода: сначала отели с явными признаками (группы «X & Smartbooking»,
    # «… Hotel …»), потом по ним узнаются названия без признаков.
    first = [(c, *suggest(c.title, c.kind)) for c in chats]
    KNOWN_NAMES.clear()
    for c, h, _ in first:
        # Известным считаем только надёжное: группу «X & Smartbooking» или
        # название с признаком отеля. Иначе «Abduvohid. Medina Town» внесло бы
        # в отели человека.
        if h and (c.kind != ChatKind.private or _MARKERS.search(h)):
            KNOWN_NAMES.setdefault(key(h), h)
    for h in hotels.values():
        KNOWN_NAMES[key(h.name)] = h.name
    known = frozenset(KNOWN_NAMES)
    raw = [(c, *suggest(c.title, c.kind, known)) for c in chats]
    # Одно название — один отель: если ключи совпали, берём имя из группового
    # чата (там его пишет сама компания), иначе самое частое.
    names: dict[str, Counter] = {}
    preferred: dict[str, str] = {}
    for c, h, _ in raw:
        if not h:
            continue
        names.setdefault(key(h), Counter())[h] += 1
        if c.kind != ChatKind.private:
            preferred.setdefault(key(h), h)
    for h in hotels.values():
        preferred[key(h.name)] = h.name  # уже заведённый отель — главнее
    out = []
    for c, h, contact in raw:
        if c.hotel_id and c.hotel_id in hotels:
            out.append((c, hotels[c.hotel_id].name, contact))
            continue
        if h:
            k = key(h)
            h = preferred.get(k) or names[k].most_common(1)[0][0]
        out.append((c, h, contact))
    return out


async def cmd_suggest(path: Path) -> int:
    async with SessionLocal() as s:
        rows = await _suggestions(s)
    # Точка с запятой и BOM — чтобы русский Excel открыл файл двойным щелчком
    # с правильными колонками и кириллицей.
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["№", "чат", "тип", "включён", "отель", "контакт"])
        for c, h, contact in rows:
            w.writerow([c.id, c.title, c.kind.value, "да" if c.enabled else "нет", h, contact])
    found = sum(1 for _, h, _ in rows if h)
    hotels = len({key(h) for _, h, _ in rows if h})
    print(f"Чатов: {len(rows)}. Отель угадан у {found}, отелей получилось {hotels}.")
    print(f"Файл: {path.resolve()}")
    print(
        "Откройте его в Excel и поправьте колонку «отель»: одинаковое название — один отель,"
        "\nпустая ячейка — «это не отель». Затем: python -m app.hotels apply " + str(path)
    )
    return 0


async def cmd_apply(path: Path) -> int:
    if not path.exists():
        print(f"Файла {path} нет. Сначала: python -m app.hotels suggest")
        return 1
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        rows = list(csv.DictReader(f, delimiter=delim))
    if not rows or "№" not in rows[0] or "отель" not in rows[0]:
        print("В файле нет колонок «№» и «отель». Возьмите файл из python -m app.hotels suggest.")
        return 1
    linked = unlinked = 0
    async with SessionLocal() as s:
        cache: dict[str, Hotel] = {}
        for r in rows:
            try:
                chat = await s.get(Chat, int(str(r["№"]).strip()))
            except ValueError:
                continue
            if chat is None:
                continue
            h = await link(s, chat, (r.get("отель") or "").strip(), (r.get("контакт") or "").strip(), cache)
            if h:
                linked += 1
            else:
                unlinked += 1
        await s.commit()
        total = (await s.execute(select(func.count(Hotel.id)))).scalar() or 0
    print(f"Привязано чатов: {linked}, без отеля: {unlinked}. Отелей в базе: {total}.")
    print("Карточки клиентов в мини-аппе уже считают переписку этих чатов.")
    return 0


async def cmd_auto() -> int:
    async with SessionLocal() as s:
        rows = await _suggestions(s)
        cache: dict[str, Hotel] = {}
        n = 0
        for c, h, contact in rows:
            if h and not c.hotel_id:
                await link(s, c, h, contact, cache)
                n += 1
        await s.commit()
        total = (await s.execute(select(func.count(Hotel.id)))).scalar() or 0
    print(f"Привязано чатов: {n}. Отелей в базе: {total}.")
    print("Проверить и поправить: python -m app.hotels suggest — и apply.")
    return 0


async def cmd_list() -> int:
    async with SessionLocal() as s:
        hotels = (await s.execute(select(Hotel).order_by(Hotel.name))).scalars().all()
        counts = dict(
            (await s.execute(
                select(Chat.hotel_id, func.count(Chat.id))
                .where(Chat.hotel_id.is_not(None)).group_by(Chat.hotel_id)
            )).all()
        )
    if not hotels:
        print("Отелей нет. Завести из чатов: python -m app.hotels suggest")
        return 0
    print("  №   чатов  тариф, сум/мес  отель")
    for h in hotels:
        print(f"{h.id:>4}   {counts.get(h.id, 0):>5}  {h.mrr:>14}  {h.name}")
    print("\nЗаполнить данные: python -m app.hotels set \"<отель>\" mrr=... city=... rooms=... tariff=...")
    return 0


_FIELDS = {"mrr": int, "rooms": int, "service_hours": float, "city": str, "tariff": str,
           "payment_note": str, "name": str}


async def cmd_set(args: list[str]) -> int:
    if len(args) < 2:
        print(__doc__)
        return 2
    name, pairs = args[0], args[1:]
    async with SessionLocal() as s:
        h = await _find(s, name)
        if h is None:
            print(f"Отеля «{name}» нет. Список: python -m app.hotels")
            return 1
        for p in pairs:
            field_name, _, value = p.partition("=")
            conv = _FIELDS.get(field_name)
            if conv is None:
                print(f"Поле «{field_name}» не знаю. Можно: {', '.join(_FIELDS)}")
                return 2
            try:
                setattr(h, field_name, conv(value.replace(" ", "")) if conv is not str else value)
            except ValueError:
                print(f"{field_name}: «{value}» — не число")
                return 2
        await s.commit()
        print(f"«{h.name}» обновлён.")
    return 0


async def _find(s: AsyncSession, name: str) -> Hotel | None:
    if name.isdigit():
        return await s.get(Hotel, int(name))
    k = key(name)
    for h in (await s.execute(select(Hotel))).scalars():
        if key(h.name) == k:
            return h
    return None


async def cmd_link(args: list[str], on: bool) -> int:
    if not args or not args[0].isdigit() or (on and len(args) < 2):
        print(__doc__)
        return 2
    async with SessionLocal() as s:
        chat = await s.get(Chat, int(args[0]))
        if chat is None:
            print(f"Чата №{args[0]} нет. Список: python -m app.chats")
            return 1
        name = " ".join(args[1:]) if on else ""
        h = await link(s, chat, name, suggest(chat.title, chat.kind)[1], {})
        await s.commit()
    print(f"«{chat.title}» → {h.name}" if h else f"«{chat.title}» отвязан от отеля")
    return 0


async def main(argv: list[str]) -> int:
    await init_db()
    cmd = argv[0] if argv else ""
    if cmd == "":
        return await cmd_list()
    if cmd == "suggest":
        return await cmd_suggest(Path(argv[1] if len(argv) > 1 else DEFAULT_FILE))
    if cmd == "apply":
        return await cmd_apply(Path(argv[1] if len(argv) > 1 else DEFAULT_FILE))
    if cmd == "auto":
        return await cmd_auto()
    if cmd == "set":
        return await cmd_set(argv[1:])
    if cmd in ("link", "unlink"):
        return await cmd_link(argv[1:], cmd == "link")
    print(__doc__)
    return 2


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
