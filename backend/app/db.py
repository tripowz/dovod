"""Подключение к базе и создание схемы.

SQLite для локальной работы, Postgres на сервере — разница только в URL.
Миграции через Alembic появятся, когда схема устоится; пока `init_db`
создаёт недостающие таблицы, потому что на пилоте база пересоздаётся чаще,
чем мигрирует.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    # SQLite не любит длинные пулы в асинхронном режиме; для Postgres
    # значения по умолчанию адекватны.
    pool_pre_ping=not settings.database_url.startswith("sqlite"),
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    # Импорт моделей обязателен до create_all: без него метаданные пусты и
    # база создаётся без таблиц — молча, без единой ошибки.
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
