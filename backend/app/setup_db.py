"""Подготовка базы в Supabase — запускается один раз с вашего компьютера.

    python -m app.setup_db                            — создать таблицы и защиту (RLS)
    python -m app.setup_db --from-sqlite dovod.db     — плюс перенести данные из SQLite

Адрес базы берётся из DATABASE_URL (строка подключения Supabase). На Vercel
схема не создаётся сама: десяток одновременных холодных стартов с ALTER TABLE
мешали бы друг другу, поэтому это отдельный шаг.

Перенос копирует все таблицы как есть, вместе с номерами строк, и переводит
счётчики Postgres за последний номер — новые записи не столкнутся со
старыми. Если в Supabase уже есть данные, перенос отказывается: слить две
базы — не то же самое, что скопировать одну.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from . import models  # noqa: F401  — регистрирует таблицы в метаданных
from .config import settings
from .db import Base, engine, init_db

CHUNK = 500


async def copy_from_sqlite(path: Path) -> int:
    src = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    total = 0
    try:
        async with engine.begin() as dst:
            for table in Base.metadata.sorted_tables:
                n = (await dst.execute(select(func.count()).select_from(table))).scalar() or 0
                if n:
                    raise SystemExit(
                        f"В базе назначения уже есть данные (таблица {table.name}: {n}). "
                        "Перенос — только в пустую базу."
                    )
        async with src.connect() as s, engine.begin() as dst:
            have = await s.run_sync(lambda c: set(inspect(c).get_table_names()))
            for table in Base.metadata.sorted_tables:
                if table.name not in have:
                    continue
                cols = await s.run_sync(
                    lambda c, t=table.name: {x["name"] for x in inspect(c).get_columns(t)}
                )
                use = [c for c in table.columns if c.name in cols]
                rows = (await s.execute(select(*use))).mappings().all()
                for i in range(0, len(rows), CHUNK):
                    await dst.execute(table.insert(), [dict(r) for r in rows[i : i + CHUNK]])
                if rows:
                    print(f"  {table.name}: {len(rows)}")
                total += len(rows)
                # Счётчик id — за последним перенесённым номером.
                if dst.dialect.name == "postgresql" and "id" in table.columns and rows:
                    await dst.execute(
                        text(
                            f"select setval(pg_get_serial_sequence('{table.name}', 'id'), "
                            f"(select max(id) from {table.name}))"
                        )
                    )
    finally:
        await src.dispose()
    return total


async def main(argv: list[str]) -> int:
    target = settings.database_url.split("@")[-1]
    print(f"База: {target}")
    await init_db()
    print("Таблицы и защита готовы.")
    if "--from-sqlite" in argv:
        i = argv.index("--from-sqlite")
        if i + 1 >= len(argv):
            print("Укажите файл: python -m app.setup_db --from-sqlite dovod.db")
            return 2
        path = Path(argv[i + 1])
        if not path.is_file():
            print(f"Файла {path} нет.")
            return 1
        if settings.database_url.startswith("sqlite"):
            print("DATABASE_URL указывает на SQLite. Впишите строку подключения Supabase.")
            return 2
        print(f"Перенос из {path}:")
        n = await copy_from_sqlite(path)
        print(f"Перенесено строк: {n}.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
