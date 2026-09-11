"""Источники: какие чаты читает «Довод».

    python -m app.chats                                  — список чатов
    python -m app.chats on 12                            — включить чат №12
    python -m app.chats on 12 --consent "Азиза, письменно, 11.09.2026"
    python -m app.chats off 12                           — выключить

Экрана «Источники» в мини-аппе пока нет — это временная замена ему. Правило
то же, что в API: личную переписку без записи о согласии не включить.
Групповой чат люди воспринимают как рабочее пространство, личный — нет.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import Chat, ChatKind

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

        if argv[0] not in ("on", "off") or len(argv) < 2:
            print(__doc__)
            return 2
        try:
            chat_id = int(argv[1])
        except ValueError:
            print(f"Номер чата — число, а не «{argv[1]}». Список: python -m app.chats")
            return 2

        chat = await s.get(Chat, chat_id)
        if chat is None:
            print(f"Чата №{chat_id} нет. Список: python -m app.chats")
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


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
