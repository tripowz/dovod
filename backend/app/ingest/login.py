"""Одноразовый вход Telethon: создаёт файл сессии.

    python -m app.ingest.login

Спросит код подтверждения, который Telegram пришлёт в приложение. Файл
сессии — это полный доступ к аккаунту: он не коммитится (см. .gitignore) и
не пересылается никому, включая разработчиков.
"""

from __future__ import annotations

import asyncio

from ..config import settings
from .reader import make_client


async def main() -> None:
    client = make_client()
    await client.start(phone=settings.telegram_phone or None)
    me = await client.get_me()
    print(f"Вход выполнен: {me.first_name} (id {me.id}).")
    print(f"Сессия сохранена: {settings.telegram_session_path}")
    print("Этот файл — полный доступ к аккаунту. Не коммитьте и не пересылайте его.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
