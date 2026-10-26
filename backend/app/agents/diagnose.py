"""Агент-диагност: технический разбор обращения для менеджера.

В отличие от `analyze` (массовая классификация сообщений из чатов), это редкий
и более глубокий вызов на одно обращение: клиент написал о проблеме — агент
готовит техменеджеру с чего начать, в чём вероятно дело, с чем связано и кого
привлечь. Пишет в таблицу `diagnoses`.

Инвариант тот же, что у всей системы: предложение, а не действие. Диагноз —
гипотеза; он ничего не назначает и никому не пишет. Ссылки на задачи и имя
исполнителя проверяются кодом по тем спискам, что даны модели: выдуманный номер
или несуществующего сотрудника в базу не пускаем.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import (
    AgentRun,
    Diagnosis,
    DiagnosisVerdict,
    Hotel,
    Knowledge,
    Module,
    Staff,
    Task,
    TaskStatus,
    Ticket,
)

log = logging.getLogger("dovod.diagnose")

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "diagnose.md").read_text(
    encoding="utf-8"
)

OPEN_STATUSES = (TaskStatus.new, TaskStatus.in_progress, TaskStatus.review)
MAX_TEXT_CHARS = 4000

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary", "probable_cause", "suggested_step", "related",
        "suggested_module", "suggested_assignee", "confidence",
    ],
    "properties": {
        "summary": {"type": "string"},
        "probable_cause": {"type": "string"},
        "suggested_step": {"type": "string"},
        "related": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ref", "why"],
                "properties": {"ref": {"type": "string"}, "why": {"type": "string"}},
            },
        },
        "suggested_module": {
            "type": "string",
            "enum": [m.value for m in Module] + [""],
        },
        "suggested_assignee": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_input(
    ticket: Ticket,
    hotel: Hotel | None,
    open_tasks: list[Task],
    recent_done: list[Task],
    team: list[Staff],
    knowledge: list[Knowledge],
    now: datetime,
) -> str:
    """Вход модели: обращение, база знаний по модулю и контекст для связей.

    Закрытые задачи даются наравне с открытыми намеренно: в уже решённой
    задаче часто лежит готовое лечение той же поломки, и диагноз «это дубль
    SB-98, прикрепить» ценнее нового расследования. База знаний — то, по чему
    модель понимает устройство системы, а не угадывает его.
    """

    def task_brief(t: Task) -> dict:
        return {
            "ref": t.ref,
            "title": t.title,
            "type": t.type.value,
            "module": t.module.value,
            "status": t.status.value,
            "assignee": t.assignee.name if t.assignee else None,
            "definition_of_done": (t.definition_of_done or "")[:300],
        }

    payload = {
        "now_utc": now.strftime("%Y-%m-%dT%H:%M"),
        "knowledge_base": [
            {
                "module": k.module.value,
                "kind": k.kind.value,
                "title": k.title,
                "source": k.source,
                "text": k.content[:1800],
            }
            for k in knowledge
        ],
        "ticket": {
            "ref": ticket.ref,
            "text": (ticket.text or "")[:MAX_TEXT_CHARS],
            "type": ticket.type.value,
            "module": ticket.module.value,
            "author": ticket.author_name,
            "author_role": ticket.author_role,
        },
        "hotel": None
        if hotel is None
        else {
            "name": hotel.name,
            "city": hotel.city,
            "tariff": hotel.tariff,
            "risk": hotel.risk.value,
        },
        "open_tasks": [task_brief(t) for t in open_tasks],
        "recent_done_tasks": [task_brief(t) for t in recent_done],
        "team": [
            {"name": s.name, "is_manager": s.is_manager} for s in team
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


_client: anthropic.AsyncAnthropic | None = None


def _anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def _clamp(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


async def feedback_examples(session: AsyncSession, limit: int = 10) -> str | None:
    """Прошлые диагнозы, которые человек отметил как «мимо» — чему не повторять.

    Именно это обещает кнопка «Мимо»: оценка учит агента, а не просто
    закрывает карточку. Берём только `wrong` с пояснением, свежие вперёд.
    """
    rows = (
        await session.execute(
            select(Ticket.text, Diagnosis.summary, Diagnosis.verdict_note)
            .join(Ticket, Ticket.id == Diagnosis.ticket_id)
            .where(Diagnosis.verdict == DiagnosisVerdict.wrong)
            .order_by(Diagnosis.verdict_at.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return None
    lines = [
        "Раньше по похожим обращениям человек отметил твой диагноз как ошибочный. "
        "Учти это и не повторяй:"
    ]
    for text, summary, note in rows:
        lines.append(
            f"- обращение «{(text or '')[:200]}» → ты сказал «{(summary or '')[:200]}», "
            f"но это мимо: {(note or '')[:300]}"
        )
    return "\n".join(lines)


async def classify(payload: str, suffix: str | None = None) -> tuple[dict | None, dict, str]:
    """Один вызов модели на одно обращение.

    `None` вместо результата — «разобрать не удалось, повторить позже»: диагноз
    при этом не создаётся, а не пишется пустым.
    """
    system = [{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}]
    if suffix:
        # Примеры ошибок меняются от оценки к оценке, поэтому идут после точки
        # кэша: новый пример не должен обнулять кэш стабильного промпта.
        system.append({"type": "text", "text": suffix})
    resp = await _anthropic().beta.messages.create(
        model=settings.model_digest,
        max_tokens=4000,
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
    if resp.stop_reason == "refusal":
        log.warning("модель отказалась ставить диагноз: %s", resp.stop_details)
        return None, usage, resp.model
    if resp.stop_reason == "max_tokens":
        log.warning("диагноз обрезан по max_tokens — повтор позже")
        return None, usage, resp.model
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text), usage, resp.model
    except json.JSONDecodeError:
        log.error("диагноз не разобрался как JSON: %.300s", text)
        return None, usage, resp.model


async def run_diagnosis(session: AsyncSession, ticket_ref: str) -> Diagnosis | None:
    """Ставит технический диагноз обращению и сохраняет его.

    Диагноз один на обращение: повторный запуск переписывает прежний на месте —
    данные о проблеме уточняются, а не копятся дублями карточек.
    """
    now = _now()
    ticket = (
        await session.execute(
            select(Ticket).options(selectinload(Ticket.hotel)).where(Ticket.ref == ticket_ref)
        )
    ).scalar_one_or_none()
    if ticket is None:
        return None
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY не задан — диагноз не поставить")
        return None

    open_tasks = (
        await session.execute(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(Task.status.in_(OPEN_STATUSES))
            .order_by(Task.last_moved_at.desc())
            .limit(60)
        )
    ).scalars().all()
    recent_done = (
        await session.execute(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(Task.status == TaskStatus.done, Task.last_moved_at >= now - timedelta(days=30))
            .order_by(Task.last_moved_at.desc())
            .limit(30)
        )
    ).scalars().all()
    team = (
        await session.execute(select(Staff).where(Staff.active.is_(True)))
    ).scalars().all()
    # База знаний по модулю обращения плюс общесистемная (UNKNOWN): известные
    # поломки — вперёд, они полезнее архитектурного обзора при живом баге.
    knowledge = (
        await session.execute(
            select(Knowledge)
            .where(Knowledge.module.in_([ticket.module, Module.unknown]))
            .order_by(Knowledge.kind, Knowledge.slug)
        )
    ).scalars().all()

    payload = build_input(
        ticket, ticket.hotel, list(open_tasks), list(recent_done),
        list(team), list(knowledge), now,
    )

    # Счётчики заводим нулями явно: default=0 — это умолчание на вставку в БД,
    # а до неё атрибут None, и += на нём падает.
    suffix = await feedback_examples(session)
    run = AgentRun(
        agent="diagnose", model=settings.model_digest, started_at=now,
        input_tokens=0, output_tokens=0, cache_read_tokens=0, items_processed=0,
    )
    session.add(run)
    try:
        result, usage, model = await classify(payload, suffix)
    except anthropic.APIError as exc:
        run.ok = False
        run.error = f"{type(exc).__name__}: {exc}"[:4000]
        run.finished_at = _now()
        await session.commit()
        log.warning("диагноз %s не поставлен: %s", ticket_ref, run.error)
        return None

    run.input_tokens += usage["input"]
    run.output_tokens += usage["output"]
    run.cache_read_tokens += usage["cache_read"]
    run.finished_at = _now()
    if result is None:
        run.ok = False
        await session.commit()
        return None
    run.items_processed = 1

    # Ссылки проверяем по тем же спискам, что видела модель: выдуманный номер
    # задачи в диагноз не попадает.
    valid_refs = {t.ref for t in open_tasks} | {t.ref for t in recent_done}
    related = [
        {"ref": str(r.get("ref")), "why": str(r.get("why") or "")[:200]}
        for r in (result.get("related") or [])
        if str(r.get("ref")) in valid_refs
    ]

    # Имя исполнителя — только из команды; иначе подсказка пустая.
    by_name = {s.name: s for s in team}
    assignee = by_name.get(str(result.get("suggested_assignee") or "").strip())

    try:
        module = Module(result.get("suggested_module") or "")
    except ValueError:
        module = Module.unknown

    existing = (
        await session.execute(select(Diagnosis).where(Diagnosis.ticket_id == ticket.id))
    ).scalar_one_or_none()
    fields = dict(
        summary=str(result.get("summary") or "")[:400],
        probable_cause=str(result.get("probable_cause") or ""),
        suggested_step=str(result.get("suggested_step") or ""),
        related=related,
        suggested_module=module,
        suggested_assignee_id=assignee.id if assignee else None,
        confidence=_clamp(result.get("confidence")),
        model=model,
    )
    if existing is None:
        existing = Diagnosis(ticket_id=ticket.id, task_id=ticket.task_id, **fields)
        session.add(existing)
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
    # Связь ставим объектом, а не только id: сессия с expire_on_commit=False
    # сохранит её после commit, и сериализатор не полезет в ленивую подгрузку
    # (в async она падает).
    existing.suggested_assignee = assignee
    await session.commit()
    return existing
