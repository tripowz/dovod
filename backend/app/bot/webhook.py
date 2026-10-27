"""Подключение бота к Vercel: Telegram будет сам присылать сообщения.

    python -m app.bot.webhook set https://dovod.vercel.app   — подключить
    python -m app.bot.webhook info                           — проверить
    python -m app.bot.webhook delete                         — отключить

Запускается один раз с вашего компьютера, нужен только BOT_TOKEN в .env.
Секрет webhook выводится из токена так же, как на Vercel, — если там не
задан свой WEBHOOK_SECRET; тогда задайте его и здесь.
"""

from __future__ import annotations

import asyncio
import sys

from ..config import settings
from ..ingest.webhook import ALLOWED_UPDATES, webhook_secret
from .telegram import BotError, call


async def main(argv: list[str]) -> int:
    if not settings.bot_token:
        print("BOT_TOKEN не задан в .env.")
        return 2
    cmd = argv[0] if argv else "info"
    try:
        if cmd == "set" and len(argv) == 2:
            base = argv[1].rstrip("/")
            if not base.startswith("https://"):
                print("Адрес должен начинаться с https://")
                return 2
            url = f"{base}/api/telegram/webhook"
            await call(
                "setWebhook",
                url=url,
                secret_token=webhook_secret(),
                allowed_updates=ALLOWED_UPDATES,
                max_connections=20,
            )
            me = await call("getMe")
            print(f"Готово: @{me['username']} присылает сообщения на {url}")  # type: ignore[index]
            if not me.get("can_read_all_group_messages"):  # type: ignore[union-attr]
                print(
                    "\nВнимание: у бота включён режим приватности — в группах он видит только команды.\n"
                    "@BotFather → /mybots → бот → Bot Settings → Group Privacy → Turn off,\n"
                    "затем удалите бота из групп и добавьте снова."
                )
            return 0
        if cmd == "delete":
            await call("deleteWebhook")
            print("Webhook отключён.")
            return 0
        if cmd == "info":
            info = await call("getWebhookInfo")
            url = info.get("url") or "не подключён"  # type: ignore[union-attr]
            print(f"Адрес: {url}")
            print(f"Ждут доставки: {info.get('pending_update_count', 0)}")  # type: ignore[union-attr]
            if err := info.get("last_error_message"):  # type: ignore[union-attr]
                print(f"Последняя ошибка: {err}")
            return 0
    except BotError as exc:
        print(f"Telegram ответил ошибкой: {exc}")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
