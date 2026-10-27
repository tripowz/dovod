"""Подключение к базе и создание схемы.

SQLite для локальной работы, Postgres на сервере — разница только в URL.
Миграции через Alembic появятся, когда схема устоится; пока `init_db`
создаёт недостающие таблицы, потому что на пилоте база пересоздаётся чаще,
чем мигрирует.
"""

from __future__ import annotations

import logging
import os
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine_args(url: str) -> dict:
    args: dict = {"echo": False}
    serverless = settings.db_null_pool or bool(os.environ.get("VERCEL"))
    if url.startswith("postgresql"):
        # Supabase-пулер (Supavisor) в режиме транзакций не держит
        # подготовленные запросы между транзакциями: кэш asyncpg выключаем, а
        # имена делаем уникальными, иначе второй вызов падает на повторе имени.
        args["connect_args"] = {
            "statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__dovod_{uuid4().hex}__",
        }
    if serverless:
        # На Vercel каждый вызов — отдельный короткий процесс: пул соединений
        # там только копит оборванные подключения.
        args["poolclass"] = NullPool
    elif not url.startswith("sqlite"):
        args["pool_pre_ping"] = True
    return args


def _url(url: str) -> str:
    # Кэш подготовленных запросов самого SQLAlchemy — тоже выключить для пулера.
    if url.startswith("postgresql+asyncpg") and "prepared_statement_cache_size" not in url:
        url += ("&" if "?" in url else "?") + "prepared_statement_cache_size=0"
    return url


engine = create_async_engine(_url(settings.database_url), **_engine_args(settings.database_url))

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
