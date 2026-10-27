"""Всё одной командой: мини-апп, бот и воркер в одном окне.

    python -m app.run

Перед стартом проверяет настройки и говорит по-человечески, чего не хватает.
Если адрес мини-аппа не задан (или это временный адрес trycloudflare, который
меняется при каждом запуске) и установлен `cloudflared`, сам поднимает туннель
и подставляет свежий адрес в кнопку бота — менять `.env` руками не нужно.

Процессы по отдельности (`app.bot.telegram`, `app.worker`, `uvicorn`) работают
как раньше; эта команда их не заменяет, а собирает вместе.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path

import uvicorn
from sqlalchemy import select

from . import seed, worker
from .bot import telegram as bot
from .config import PROJECT_ROOT, settings
from .db import SessionLocal, init_db
from .models import Staff

log = logging.getLogger("dovod.run")

DIST = PROJECT_ROOT / "miniapp" / "dist" / "index.html"
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def line(ok: bool | None, title: str, hint: str = "") -> None:
    mark = {True: "OK  ", False: "НЕТ ", None: "ВНИМ"}[ok]
    print(f"  [{mark}] {title}")
    if hint:
        for h in hint.splitlines():
            print(f"         {h}")


async def preflight() -> bool:
    """Проверки перед стартом. False — запускать нечего."""
    print("\nПроверка настроек")
    fatal = False

    bot_name = ""
    if not settings.bot_token:
        line(False, "BOT_TOKEN", "Создайте бота у @BotFather и впишите токен в .env.")
        fatal = True
    else:
        try:
            me = await bot.call("getMe")
            bot_name = me.get("username", "")  # type: ignore[union-attr]
            line(True, f"бот @{bot_name}")
        except Exception as exc:  # noqa: BLE001
            line(False, "BOT_TOKEN не принят Telegram", f"{exc}\nПроверьте токен у @BotFather.")
            fatal = True

    if not settings.manager_tg_user_id:
        line(
            False,
            "MANAGER_TG_USER_ID",
            f"Напишите боту {'@' + bot_name if bot_name else ''} /id и впишите число в .env.\n"
            "Пока его нет, бот ответит на /id, но в мини-апп никто не войдёт.",
        )
    else:
        await seed.main(demo=False, quiet=True)  # руководитель и правила; повторный запуск ничего не ломает
        async with SessionLocal() as s:
            boss = (
                await s.execute(
                    select(Staff).where(Staff.tg_user_id == settings.manager_tg_user_id)
                )
            ).scalar_one_or_none()
        line(boss is not None, f"руководитель: {boss.name if boss else '—'}")

    if DIST.is_file():
        line(True, "сборка мини-аппа")
    else:
        line(
            False,
            "сборка мини-аппа не найдена",
            "cd ..\\miniapp; npm install; npm run build",
        )

    has_session = Path(settings.telegram_session_path).exists()
    if settings.telegram_api_id and settings.telegram_api_hash and has_session:
        line(True, "чтение чатов (Telethon)")
    else:
        line(
            None,
            "чтение чатов выключено",
            "Нужны TELEGRAM_API_ID, TELEGRAM_API_HASH и вход: python -m app.ingest.login",
        )

    line(
        True if settings.anthropic_api_key else None,
        "разбор моделью" if settings.anthropic_api_key else "ANTHROPIC_API_KEY не задан",
        "" if settings.anthropic_api_key else "Сообщения будут копиться без разбора.",
    )
    return not fatal


def find_cloudflared() -> str | None:
    """cloudflared в PATH или в стандартных папках установки.

    После `winget install` PATH обновляется только в новом окне терминала —
    в старом программа есть, а `which` её не видит.
    """
    if exe := shutil.which("cloudflared"):
        return exe
    for base in (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
    ):
        p = Path(base) / "cloudflared" / "cloudflared.exe"
        for cand in (p, Path(base) / "cloudflared.exe"):
            if cand.is_file():
                return str(cand)
    return None


async def start_tunnel() -> asyncio.subprocess.Process | None:
    """Поднимает cloudflared и подставляет его адрес в settings.miniapp_url."""
    fixed = settings.miniapp_url.startswith("https://") and "trycloudflare.com" not in settings.miniapp_url
    if fixed:
        line(True, f"адрес мини-аппа: {settings.miniapp_url}")
        return None
    exe = find_cloudflared()
    if exe is None:
        line(
            None,
            "кнопка мини-аппа не появится",
            "MINIAPP_URL не задан, а cloudflared не установлен.\n"
            "Установите: winget install Cloudflare.cloudflared — и перезапустите.\n"
            "Бот при этом работает: /id, /digest, /report.",
        )
        return None

    proc = await asyncio.create_subprocess_exec(
        exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{settings.api_port}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    url = ""
    try:
        async with asyncio.timeout(40):
            while not url:
                raw = await proc.stderr.readline()  # type: ignore[union-attr]
                if not raw:
                    break
                if m := TUNNEL_URL.search(raw.decode(errors="replace")):
                    url = m.group(0)
    except TimeoutError:
        pass
    if not url:
        line(None, "туннель не поднялся за 40 с", "Кнопка мини-аппа не появится. Бот работает.")
        proc.kill()
        return None

    # Дальше cloudflared пишет в stderr постоянно; читаем и выбрасываем, иначе
    # переполненный буфер его остановит.
    async def drain() -> None:
        while await proc.stderr.readline():  # type: ignore[union-attr]
            pass

    asyncio.create_task(drain())
    settings.miniapp_url = url
    line(True, f"туннель: {url}")
    return proc


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    await init_db()

    if not await preflight():
        print("\nЗапуск остановлен: исправьте пункты «НЕТ» выше.")
        return 1
    tunnel = await start_tunnel()

    from .main import app  # после туннеля: так приложение видит итоговый адрес

    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="warning")
    )
    await bot.setup()
    await worker.start()
    print(
        f"\nДовод запущен.\n  Сайт в браузере: http://localhost:{settings.api_port}"
        + (f"\n  Снаружи (и мини-апп в Telegram): {settings.miniapp_url}" if settings.miniapp_url else "")
        + "\n  Вход на сайте — через Telegram, кодом от бота.\nОстановить: Ctrl+C\n"
    )
    try:
        await asyncio.gather(server.serve(), bot.run_polling())
    finally:
        if tunnel is not None and tunnel.returncode is None:
            tunnel.kill()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nОстановлено.")
