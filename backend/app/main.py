"""FastAPI-приложение «Довода».

Разбор чатов живёт в отдельном процессе (`python -m app.worker`) — здесь
только HTTP для мини-аппа.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import PROJECT_ROOT, settings
from .db import init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Довод",
    description="Трекер задач в Telegram: чат → задача → доведённая работа.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    """Что реально доступно, а не только «жив».

    Выключенная из-за отсутствия токена возможность иначе выглядит как
    работающая, просто ничего не делающая, — и это самый неприятный вид
    поломки, потому что его не видно.
    """
    checks = {
        "bot_token": bool(settings.bot_token),
        "anthropic_key": bool(settings.anthropic_api_key),
        "telegram_configured": bool(settings.telegram_api_id and settings.telegram_api_hash),
        "telegram_session": Path(settings.telegram_session_path).exists(),
        "miniapp_url_https": settings.miniapp_url.startswith("https://"),
    }
    degraded = [k for k, ok in checks.items() if not ok]
    return {
        "status": "ok" if not degraded else "degraded",
        "degraded": degraded,
        "checks": checks,
        "telethon_read_only": settings.telethon_read_only,
        "notes": {
            "bot_token": "Без него вход в мини-апп невозможен: подпись initData не проверить.",
            "miniapp_url_https": "Telegram не откроет мини-апп по http или localhost.",
        },
    }


# --- статика мини-аппа -----------------------------------------------------
# Блок обязан идти последним: "/{full_path:path}" подходит подо всё, и
# объявленный выше него /health возвращал бы index.html вместо JSON.
DIST = PROJECT_ROOT / "miniapp" / "dist"

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = (DIST / full_path).resolve()
        # Не выпускаем за пределы dist: full_path приходит из запроса.
        if full_path and candidate.is_file() and candidate.is_relative_to(DIST):
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:
    logging.getLogger(__name__).warning(
        "Сборка мини-аппа не найдена (%s). Соберите: cd miniapp && npm run build", DIST
    )
