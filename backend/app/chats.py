"""Источники: какие чаты читает «Довод».

    python -m app.chats                                  — список чатов
    python -m app.chats on 12                            — включить чат №12
    python -m app.chats on 12 --consent "Азиза, письменно, 11.09.2026"
    python -m app.chats off 12                           — выключить
    python -m app.chats on all                           — включить все групповые
    python -m app.chats on all --history                 — то же, с историей
    python -m app.chats on all --private --consent "служебный аккаунт, рабочая переписка"
                                                         — включить и личные
    python -m app.chats off all                          — выключить все
    python -m app.chats history 30                       — догрузить последние 30 дней

Массовое включение по умолчанию читает чаты с текущего момента: история
сотен чатов разом встала бы в очередь разбора перед свежими обращениями
клиентов. Личные переписки массово включаются только с `--private` и
записью о согласии — она сохраняется в каждом чате. Служебный чат Telegram
с кодами входа не включается никогда.

Экрана «Источники» в мини-аппе пока нет — это временная замена ему. Правило
то же, что в API: личную переписку без записи о согласии не включить.
Групповой чат люди воспринимают как рабочее пространство, личный — нет.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .models import FROM_NOW, TELEGRAM_SERVICE_ID, Chat, ChatKind

KIND = {
    ChatKind.support: "поддержка",
    ChatKind.team: "команда",
    ChatKind.dev: "разработка",
    ChatKind.sales: "продажи",
    ChatKind.private: "ЛИЧНАЯ",
    ChatKind.other: "другое",
}


async def main(argv: list[str]) -> int:
    await init_db()
    async with SessionLocal() as s:
        if not argv:
            rows = (
                await s.execute(select(Chat).order_by(Chat.enabled.desc(), Chat.title))
            ).scalars().all()
            if not rows:
                print(
                    "Чатов пока нет. Они появляются после первого запуска воркера:\n"
                    "    python -m app.worker"
                )
                return 0
            print("  №   чтение  тип          название")
            for c in rows:
                state = "ВКЛ  " if c.enabled else "выкл "
                print(f"{c.id:>4}   {state}  {KIND[c.kind]:<11}  {c.title}")
            print(
                "\nВключить: python -m app.chats on <№>. "
                "Для личных нужен --consent \"от кого и когда согласие\"."
            )
            return 0

        if argv[0] == "history":
            return await history(s, argv[1:])
        if argv[0] not in ("on", "off") or len(argv) < 2:
            print(__doc__)
            return 2
        if argv[1] == "all":
            consent = ""
            if "--consent" in argv:
                consent = " ".join(argv[argv.index("--consent") + 1 :]).strip()
            return await bulk(
                s, argv[0] == "on", "--history" in argv, "--private" in argv, consent
            )
        try:
            chat_id = int(argv[1])
        except ValueError:
            print(f"Номер чата — число, а не «{argv[1]}». Список: python -m app.chats")
            return 2

        chat = await s.get(Chat, chat_id)
        if chat is None:
            print(f"Чата №{chat_id} нет. Список: python -m app.chats")
            return 1

        if argv[0] == "on" and chat.tg_chat_id == TELEGRAM_SERVICE_ID:
            print("Это служебный чат Telegram с кодами входа. Его читать нельзя.")
            return 1

        if argv[0] == "off":
            chat.enabled = False
            await s.commit()
            print(f"«{chat.title}» выключен. Уже сохранённые сообщения остаются.")
            return 0

        consent = ""
        if "--consent" in argv:
            i = argv.index("--consent")
            consent = " ".join(argv[i + 1 :]).strip()
        if chat.kind == ChatKind.private and not consent:
            print(
                f"«{chat.title}» — личная переписка. Включение требует письменного "
                "согласия сотрудника. Укажите, от кого и когда оно получено:\n"
                f"    python -m app.chats on {chat.id} --consent \"Имя, письменно, дата\""
            )
            return 1

        chat.enabled = True
        if consent:
            chat.consent_note = consent
        await s.commit()
        print(
            f"«{chat.title}» включён. Воркер начнёт читать его на следующем проходе "
            "(раз в 15 минут) и заберёт до 200 последних сообщений."
        )
        return 0


async def bulk(s, on: bool, history: bool, private: bool = False, consent: str = "") -> int:
    if on and private and not consent:
        print(
            "Личные переписки включаются только с записью о согласии. Укажите, чья "
            "это переписка и на каком основании она читается:\n"
            '    python -m app.chats on all --private --consent '
            '"служебный аккаунт Smartbooking, рабочая переписка с отелями"'
        )
        return 1
    rows = (await s.execute(select(Chat))).scalars().all()
    if not on:
        n = sum(1 for c in rows if c.enabled)
        for c in rows:
            c.enabled = False
        await s.commit()
        print(f"Выключено чатов: {n}. Уже сохранённые сообщения остаются.")
        return 0

    enabled = skipped_private = enabled_private = 0
    for c in rows:
        if c.tg_chat_id == TELEGRAM_SERVICE_ID or c.enabled:
            continue
        if c.kind == ChatKind.private:
            if not private:
                skipped_private += 1
                continue
            c.consent_note = consent
            enabled_private += 1
        c.enabled = True
        # Чат, который ещё ни разу не читали, начинаем с текущего момента.
        if not history and c.last_seen_msg_id == 0:
            c.last_seen_msg_id = FROM_NOW
        enabled += 1
    await s.commit()

    print(f"Включено чатов: {enabled}, из них личных: {enabled_private}.")
    if enabled_private:
        print(f"Запись о согласии: «{consent}».")
    if history:
        print(
            f"История: до {settings.backfill_limit} последних сообщений из каждого. "
            "Свежие обращения будут ждать, пока разбор не пройдёт старые."
        )
    else:
        print("Читаются с текущего момента: старые сообщения не берутся.")
    if skipped_private:
        print(
            f"Личных переписок пропущено: {skipped_private}. Включить их все:\n"
            '    python -m app.chats on all --private --consent "основание"\n'
            "или по одной: python -m app.chats on <№> --consent \"Имя, письменно, дата\""
        )
    print("Воркер подхватит изменения на следующем проходе (раз в 15 минут).")
    return 0


async def history(s, args: list[str]) -> int:
    try:
        days = int(args[0]) if args else 30
    except ValueError:
        print(f"Число дней — число, а не «{args[0]}». Например: python -m app.chats history 30")
        return 2
    if not 1 <= days <= 90:
        print("Период — от 1 до 90 дней.")
        return 2

    from .ingest.reader import fetch_history, make_client

    client = make_client()
    # Сотни чатов подряд — Telegram может попросить подождать. Ждём сами,
    # а не падаем на середине.
    client.flood_sleep_threshold = 300
    print(
        f"Загружаю историю за {days} дн. из включённых чатов. Воркер должен быть "
        "остановлен: одну сессию нельзя открыть дважды."
    )
    await client.start(phone=settings.telegram_phone or None)
    try:
        read, stored = await fetch_history(client, s, days)
    finally:
        await client.disconnect()

    batches = -(-stored // settings.analyze_batch_size)
    per_cycle = settings.analyze_batch_size * 10
    hours = stored / per_cycle * settings.analyze_interval_minutes / 60
    print(f"\nЧатов с сообщениями за период: {read}. Загружено сообщений: {stored}.")
    print(
        f"Разбор: до ~{batches} вызовов модели (часть отсеет фильтр), "
        f"около {hours:.0f} ч при {per_cycle} сообщениях за проход."
    )
    print(
        f"Свежие сообщения разбираются вперёд истории. Из сообщений старше "
        f"{settings.fresh_hours} ч заявки с SLA не создаются."
    )
    print("Теперь запустите воркер: python -m app.worker")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
