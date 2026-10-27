"""Всё одной командой: сайт, чтение чатов и разбор в одном окне.

    python -m app.run

Перед стартом проверяет настройки и говорит по-человечески, чего не хватает.
При первом запуске выдаёт руководителю логин и пароль для сайта.

Бот не обязателен. Если он подключён (BOT_TOKEN), работает и он: вход кодом,
мини-апп в Telegram, сводка в 09:00. Если адрес сайта не задан (или он
временный, trycloudflare) и установлен `cloudflared`, команда сама поднимает
туннель, чтобы сайт открывался не только на этом компьютере.

Процессы по отдельности (`app.bot.telegram`, `app.worker`, `uvicorn`) работают
как раньше; эта команда их не заменяет, а собирает вместе.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

import uvicorn
from sqlalchemy import select

from . import passwords, seed, worker
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

    if settings.bot_token:
        try:
            me = await bot.call("getMe")
            line(True, f"бот @{me.get('username', '')} — по желанию: вход кодом и мини-апп в Telegram")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            line(None, "BOT_TOKEN не принят Telegram — бот выключен", f"{exc}")
            settings.bot_token = ""
    else:
        line(True, "без бота: вход на сайт по логину и паролю")

    await seed.main(demo=False, quiet=True)  # правила; повторный запуск ничего не ломает
    boss, password = await ensure_manager()
    if boss is None:
        line(False, "руководитель", "Впишите в .env MANAGER_NAME — имя руководителя.")
        fatal = True
    elif password:
        line(True, f"руководитель: {boss.name}")
        print(
            "\n  ┌─ Вход на сайт ─────────────────────────────\n"
            f"  │  логин:  {boss.login}\n"
            f"  │  пароль: {password}\n"
            "  │  Запишите: пароль показывается один раз.\n"
            "  │  Новый — в «Команде» на сайте или: python -m app.users password " + str(boss.login) + "\n"
            "  └────────────────────────────────────────────\n"
        )
    else:
        line(True, f"руководитель: {boss.name} (логин {boss.login})")

    if DIST.is_file():
        line(True, "сборка сайта")
    else:
        line(
            False,
            "сборка сайта не найдена",
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


async def ensure_manager() -> tuple[Staff | None, str]:
    """Руководитель с логином и паролем. Пароль — только если он создан сейчас."""
    async with SessionLocal() as s:
        q = select(Staff).where(Staff.is_manager.is_(True), Staff.active.is_(True))
        if settings.manager_tg_user_id:
            q = select(Staff).where(Staff.tg_user_id == settings.manager_tg_user_id)
        boss = (await s.execute(q.order_by(Staff.id))).scalars().first()
        if boss is None:
            if not settings.manager_name.strip():
                return None, ""
            # Без Telegram ID — заглушка: отрицательных ID в Telegram не бывает.
            boss = Staff(
                tg_user_id=settings.manager_tg_user_id or -secrets.randbelow(10**12) - 10**9,
                name=settings.manager_name.strip(),
                is_manager=True,
            )
            s.add(boss)
            await s.flush()
        password = ""
        if not boss.login:
            boss.login = passwords.login_from_name(boss.name)
        if not boss.password_hash:
            password = passwords.generate_password()
            boss.password_hash = passwords.hash_password(password)
        await s.commit()
        await s.refresh(boss)
        return boss, password


async def start_tunnel() -> asyncio.subprocess.Process | None:
    """Поднимает cloudflared и подставляет его адрес в settings.miniapp_url."""
    fixed = settings.miniapp_url.startswith("https://") and "trycloudflare.com" not in settings.miniapp_url
    if fixed:
        line(True, f"адрес сайта: {settings.miniapp_url}")
        return None
    exe = find_cloudflared()
    if exe is None:
        line(
            None,
            "сайт доступен только на этом компьютере",
            "Чтобы открыть его команде: winget install Cloudflare.cloudflared — и перезапустите\n"
            "(временный адрес), или разверните на сервере с доменом (DEPLOY.md).\n"
            "Сайт на этом компьютере работает: http://localhost:" + str(settings.api_port),
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
        line(None, "туннель не поднялся за 40 с", "Снаружи сайт недоступен; на этом компьютере работает.")
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
    if settings.bot_token:
        await bot.setup()
    await worker.start()
    print(
        f"\nДовод запущен.\n  Сайт: http://localhost:{settings.api_port}"
        + (f"\n  Снаружи, для команды: {settings.miniapp_url}" if settings.miniapp_url else "")
        + "\nОстановить: Ctrl+C\n"
    )
    jobs = [server.serve()]
    if settings.bot_token:
        jobs.append(bot.run_polling())
    try:
        await asyncio.gather(*jobs)
    finally:
        if tunnel is not None and tunnel.returncode is None:
            tunnel.kill()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nОстановлено.")
