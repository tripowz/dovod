"""Подключение к базе и создание схемы.

SQLite для локальной работы, Postgres на сервере — разница только в URL.
Миграции через Alembic появятся, когда схема устоится; пока `init_db`
создаёт недостающие таблицы, потому что на пилоте база пересоздаётся чаще,
чем мигрирует.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
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
        await conn.run_sync(_add_missing_columns)
        if conn.dialect.name == "postgresql":
            await conn.run_sync(_lock_public_api)


def _lock_public_api(conn) -> None:
    """Включает RLS на всех таблицах схемы public.

    Supabase сам публикует таблицы через REST API, и без RLS любой с
    публичным ключом проекта прочитал бы переписку с клиентами. Политик нет
    намеренно: API не отдаёт ничего, а бэкенд подключается владельцем
    таблиц, и на него RLS не действует. На обычном Postgres это безвредно.
    """
    for (name,) in conn.execute(
        text(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"
        )
    ):
        conn.execute(text(f'alter table public."{name}" enable row level security'))
        logging.getLogger("dovod.db").info("RLS включён: %s", name)


def _add_missing_columns(conn) -> None:
    """Досоздаёт новые колонки в уже существующих таблицах.

    `create_all` создаёт только недостающие таблицы: колонка, добавленная в
    модель позже, в живой базе не появится, и первый же запрос упадёт. До
    Alembic это закрывает самый частый случай — новое необязательное поле.
    Внешний ключ при этом не создаётся: SQLite не умеет добавлять его
    ALTER-ом, а для чтения данных он не нужен.
    """
    insp = inspect(conn)
    log = logging.getLogger("dovod.db")
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            if not col.nullable and col.server_default is None:
                log.error(
                    "в таблице %s нет обязательной колонки %s — нужна миграция",
                    table.name, col.name,
                )
                continue
            ddl = col.type.compile(dialect=conn.dialect)
            conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))
            log.info("добавлена колонка %s.%s", table.name, col.name)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
