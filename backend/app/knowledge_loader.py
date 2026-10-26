"""Загрузка базы знаний из markdown-файлов `app/knowledge/` в таблицу.

    python -m app.knowledge_loader

Источник правды — файлы под гитом: их видно в ревью и правит человек. Таблица
`knowledge` — их загруженная копия для выдачи агенту. Загрузка идемпотентна:
запись переписывается по `slug` (имя файла), а не плодится при каждом прогоне,
и записи, чей файл удалили, из таблицы убираются — иначе база копила бы знания,
которых в коде уже нет.

Frontmatter простой, построчный (`ключ: значение`), без зависимости от YAML:
одна библиотека ради пяти полей дороже, чем этот разбор.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import Knowledge, KnowledgeKind, Module

log = logging.getLogger("dovod.knowledge")

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"


def _parse(text: str) -> tuple[dict[str, str], str]:
    """Разбирает `--- frontmatter --- тело`. Без frontmatter — всё это тело."""
    if not text.startswith("---"):
        return {}, text.strip()
    _, _, rest = text.partition("---")
    front, sep, body = rest.partition("---")
    if not sep:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in front.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip()
    return meta, body.strip()


def parse_file(path: Path) -> dict:
    meta, body = _parse(path.read_text(encoding="utf-8"))
    try:
        module = Module(meta.get("module", "UNKNOWN"))
    except ValueError:
        module = Module.unknown
    try:
        kind = KnowledgeKind(meta.get("kind", "architecture"))
    except ValueError:
        kind = KnowledgeKind.architecture
    return {
        "slug": path.stem,
        "module": module,
        "kind": kind,
        "title": meta.get("title", path.stem),
        "source": meta.get("source", "")[:500],
        "content": body,
    }


async def load_knowledge(session) -> dict:
    """Синхронизирует таблицу с файлами. Возвращает счётчики."""
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    on_disk = {p.stem: parse_file(p) for p in files}

    existing = {
        k.slug: k for k in (await session.execute(select(Knowledge))).scalars()
    }
    added = updated = removed = 0

    for slug, data in on_disk.items():
        row = existing.get(slug)
        if row is None:
            session.add(Knowledge(**data))
            added += 1
        else:
            for k, v in data.items():
                setattr(row, k, v)
            updated += 1

    for slug, row in existing.items():
        if slug not in on_disk:
            await session.delete(row)
            removed += 1

    await session.commit()
    return {"added": added, "updated": updated, "removed": removed, "total": len(on_disk)}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    await init_db()
    async with SessionLocal() as s:
        stats = await load_knowledge(s)
    log.info("база знаний: %s", stats)


if __name__ == "__main__":
    asyncio.run(main())
