"""Агент-обучение: извлекает многоразовое знание из закрытой задачи.

Слой 3 базы знаний — самопополнение. Когда задача-баг/проблема закрыта, в её
истории и заметках часто лежит готовое лечение, которое пригодится на похожем
обращении. Этот агент его дистиллирует в запись базы (`generated=True`), и в
следующий раз диагност отвечает «это как в SB-412, лечится так», а не «смотрите
логи».

Инвариант базы: мусорная запись хуже пустоты. Если в задаче нет обобщаемого
урока (разовая правка, нет причины/решения в тексте), агент возвращает
`reusable=false`, и в базу ничего не пишется — выдуманный «урок» увёл бы
будущий диагноз не туда с уверенным видом.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import (
    AgentRun,
    Knowledge,
    KnowledgeKind,
    Module,
    Task,
    ThreadLink,
)

log = logging.getLogger("dovod.learn")

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "learn.md").read_text(
    encoding="utf-8"
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reusable", "module", "title", "symptom", "cause", "fix"],
    "properties": {
        "reusable": {"type": "boolean"},
        "module": {"type": "string", "enum": [m.value for m in Module] + [""]},
        "title": {"type": "string"},
        "symptom": {"type": "string"},
        "cause": {"type": "string"},
        "fix": {"type": "string"},
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_input(task: Task) -> str:
    """Вход модели: закрытая задача со всем, из чего можно вычитать урок."""
    payload = {
        "ref": task.ref,
        "title": task.title,
        "definition_of_done": (task.definition_of_done or "")[:600],
        "type": task.type.value,
        "module": task.module.value,
        "notes": [
            {"role": n.role.value, "author": n.author_name, "text": (n.text or "")[:1000]}
            for n in task.notes
        ],
        "events": [e.text for e in task.events if not e.pending][:30],
        "thread": [
            {
                "tag": link.tag,
                "author": link.message.author_name if link.message else None,
                "text": (link.message.text or "")[:800] if link.message else "",
            }
            for link in task.thread
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


_client: anthropic.AsyncAnthropic | None = None


def _anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def classify(payload: str) -> tuple[dict | None, dict, str]:
    system = [{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}]
    resp = await _anthropic().beta.messages.create(
        model=settings.model_digest,
        max_tokens=2000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": settings.effort_digest,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        system=system,
        messages=[{"role": "user", "content": payload}],
    )
    usage = {
        "input": resp.usage.input_tokens,
        "output": resp.usage.output_tokens,
        "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    }
    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("обучение не удалось (%s) по задаче", resp.stop_reason)
        return None, usage, resp.model
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text), usage, resp.model
    except json.JSONDecodeError:
        log.error("ответ обучения не разобрался как JSON: %.300s", text)
        return None, usage, resp.model


def _content(result: dict) -> str:
    """Тело записи базы из разбора: симптом, причина, что помогло."""
    parts = []
    if result.get("symptom"):
        parts.append(f"**Симптом.** {result['symptom']}")
    if result.get("cause"):
        parts.append(f"**Причина.** {result['cause']}")
    if result.get("fix"):
        parts.append(f"**Что помогло.** {result['fix']}")
    return "\n\n".join(parts)


async def distill_task(session: AsyncSession, task_ref: str) -> Knowledge | None:
    """Извлекает знание из закрытой задачи и кладёт в базу. None — урока нет.

    Одна запись на задачу: slug `task-<ref>`, повторный прогон переписывает её
    на месте, а не плодит дубли.
    """
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY не задан — обучение из задач выключено")
        return None
    task = (
        await session.execute(
            select(Task)
            .options(
                selectinload(Task.notes),
                selectinload(Task.events),
                selectinload(Task.thread).selectinload(ThreadLink.message),
            )
            .where(Task.ref == task_ref)
        )
    ).scalar_one_or_none()
    if task is None:
        return None

    run = AgentRun(
        agent="learn", model=settings.model_digest, started_at=_now(),
        input_tokens=0, output_tokens=0, cache_read_tokens=0, items_processed=0,
    )
    session.add(run)
    try:
        result, usage, model = await classify(build_input(task))
    except anthropic.APIError as exc:
        run.ok = False
        run.error = f"{type(exc).__name__}: {exc}"[:4000]
        run.finished_at = _now()
        await session.commit()
        log.warning("обучение по %s упало: %s", task_ref, run.error)
        return None

    run.input_tokens += usage["input"]
    run.output_tokens += usage["output"]
    run.cache_read_tokens += usage["cache_read"]
    run.finished_at = _now()
    run.items_processed = 1

    slug = f"task-{task.ref.lower()}"
    existing = (
        await session.execute(select(Knowledge).where(Knowledge.slug == slug))
    ).scalar_one_or_none()

    if result is None or not result.get("reusable"):
        # Обобщаемого урока нет. Пишем неактивную метку: её не выдаёт диагност,
        # но по ней видно, что задача разобрана, и модель не тратится на неё
        # снова. Без метки отбор возвращал бы задачу каждый проход.
        marker = dict(
            module=task.module,
            kind=KnowledgeKind.solution,
            title=f"{task.ref}: без обобщаемого урока",
            content="",
            source=f"закрытая задача {task.ref}",
            generated=True,
            task_ref=task.ref,
            active=False,
        )
        if existing is None:
            session.add(Knowledge(slug=slug, **marker))
        else:
            for k, v in marker.items():
                setattr(existing, k, v)
        await session.commit()
        return None

    try:
        module = Module(result.get("module") or "")
    except ValueError:
        module = task.module

    fields = dict(
        module=module,
        kind=KnowledgeKind.solution,
        title=str(result.get("title") or task.title)[:300],
        content=_content(result),
        source=f"закрытая задача {task.ref}",
        generated=True,
        task_ref=task.ref,
        active=True,
    )
    if existing is None:
        existing = Knowledge(slug=slug, **fields)
        session.add(existing)
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
    await session.commit()
    return existing
