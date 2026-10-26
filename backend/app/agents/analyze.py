"""Разбор сообщений из чатов: новый запрос, продолжение задачи или разговор.

Два этапа, потому что модель на каждом сообщении из всех рабочих чатов — это
тысячи вызовов в неделю:

1. Дешёвый фильтр без модели отсеивает явные разговоры («ок», «спасибо»,
   эмодзи). Короткие сообщения с рабочими словами («не работает», «сделал»)
   он пропускает: это самые важные сообщения в чате, и терять их ради
   экономии нельзя.
2. Остаток уходит в модель пачкой вместе со списком открытых задач —
   без него модель физически не может узнать продолжение.

Каждое сообщение получает исход, включая отсеянные фильтром: иначе счётчик
«отсеяно» врёт, а сообщение без исхода выглядит потерянным.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import (
    AgentRun,
    Chat,
    Message,
    Module,
    Proposal,
    ProposalFeedback,
    ProposalKind,
    ProposalState,
    Task,
    TaskStatus,
    TaskType,
    ThreadLink,
)

log = logging.getLogger("dovod.analyze")

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "analyze.md").read_text(
    encoding="utf-8"
)

OPEN_STATUSES = (TaskStatus.new, TaskStatus.in_progress, TaskStatus.review)
CONTINUATIONS = {
    ProposalKind.progress,
    ProposalKind.deadline,
    ProposalKind.scope,
    ProposalKind.duplicate,
    ProposalKind.question,
}

_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "message_id", "kind", "task_ref", "link_basis",
                    "reading", "action", "confidence", "draft",
                ],
                "properties": {
                    "message_id": {"type": "integer"},
                    "kind": {"type": "string", "enum": [k.value for k in ProposalKind]},
                    "task_ref": _NULLABLE_STR,
                    "link_basis": {"type": "string", "enum": ["reply", "mention", "semantic", ""]},
                    "reading": {"type": "string"},
                    "action": {"type": "string"},
                    "confidence": {"type": "number"},
                    "draft": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "dod", "type", "module", "due_at", "dod_add"],
                        "properties": {
                            "title": {"type": "string"},
                            "dod": {"type": "string"},
                            "type": {"type": "string", "enum": [t.value for t in TaskType] + [""]},
                            "module": {"type": "string", "enum": [m.value for m in Module] + [""]},
                            "due_at": _NULLABLE_STR,
                            "dod_add": _NULLABLE_STR,
                        },
                    },
                },
            },
        }
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Этап 1: фильтр без модели
# --------------------------------------------------------------------------

_NOISE = re.compile(
    r"^(ок|окей|ok|okay|спасибо|спс|рахмат|благодарю|понял|поняла|принял|приняла|"
    r"хорошо|ладно|да|нет|ага|угу|супер|отлично|класс)[\s!.,)]*$",
    re.IGNORECASE,
)
# Слова, по которым короткое сообщение оказывается рабочим. Узбекские формы —
# потому что отельеры пишут и так: «ishlamayapti» — «не работает».
_WORK = re.compile(
    r"сломал|не\s*работ|ошибк|баг|глюч|виснет|пропал|не\s*приход|не\s*приш|"
    r"сделал|готов|выкат|почин|исправ|перенес|срок|проверь|ishlamay|xato",
    re.IGNORECASE,
)
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def prefilter_reason(msg: Message) -> str | None:
    """Причина отсеять сообщение без модели или None, если его надо разобрать."""
    text = (msg.text or "").strip()
    if not text:
        return (
            "картинка или файл без подписи — чтение скриншотов пока не подключено"
            if msg.media_kind
            else "пустое сообщение"
        )
    if not _HAS_LETTER.search(text):
        return "эмодзи или знаки без текста"
    if _NOISE.match(text):
        return "короткая реплика без содержания"
    if (
        len(text) < settings.prefilter_min_chars
        and not msg.reply_to_msg_id
        and not _WORK.search(text)
    ):
        return "короткая реплика без рабочих слов"
    return None


# --------------------------------------------------------------------------
# Этап 2: модель
# --------------------------------------------------------------------------

# Длиннее — это лог или выгрузка, а не запрос; хвост для классификации не
# нужен, а пачку он раздувает кратно.
MAX_TEXT_CHARS = 4000


def build_input(
    chat_title: str,
    msgs: list[Message],
    tasks: list[Task],
    reply_links: dict[int, str],
    now: datetime,
) -> str:
    """Вход модели.

    Время и задачи идут в сообщение пользователя, а не в системный промпт:
    системный остаётся неизменным и может кэшироваться.
    """
    payload = {
        "chat": chat_title,
        "now_utc": now.strftime("%Y-%m-%dT%H:%M"),
        "open_tasks": [
            {
                "ref": t.ref,
                "title": t.title,
                "status": t.status.value,
                "assignee": t.assignee.name if t.assignee else None,
                "due_at": t.due_at.strftime("%Y-%m-%d") if t.due_at else None,
                "definition_of_done": t.definition_of_done[:300],
            }
            for t in tasks
        ],
        "messages": [
            {
                "message_id": m.id,
                "author": m.author_name,
                "sent_at": m.sent_at.strftime("%Y-%m-%dT%H:%M"),
                "text": m.text[:MAX_TEXT_CHARS],
                "reply_to_task": reply_links.get(m.reply_to_msg_id) if m.reply_to_msg_id else None,
            }
            for m in msgs
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


async def feedback_examples(session: AsyncSession, limit: int = 20) -> str | None:
    """Отклонённые людьми предложения — примеры того, чего не повторять.

    Именно это обещает кнопка «Не то»: отказ учит разбор, а не просто
    закрывает карточку.
    """
    rows = (
        await session.execute(
            select(ProposalFeedback)
            .where(ProposalFeedback.active.is_(True))
            .order_by(ProposalFeedback.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return None
    lines = ["Люди отклонили эти твои предложения. Учти это на похожих сообщениях."]
    for f in rows:
        line = f"- «{f.message_text[:300]}» — ты предложил «{f.was_kind}»"
        if f.correct_kind:
            line += f", правильно «{f.correct_kind}»"
        if f.note:
            line += f". Комментарий: {f.note}"
        lines.append(line)
    return "\n".join(lines)


_client: anthropic.AsyncAnthropic | None = None


def _anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def classify(payload: str, suffix: str | None) -> tuple[list[dict] | None, dict, str]:
    """Один вызов модели на пачку сообщений.

    Возвращает (результаты, расход токенов, модель). `None` вместо результатов
    означает «разобрать не удалось, повторить на следующем проходе» —
    сообщения при этом остаются неразобранными, а не помечаются как пустые.
    """
    system: list[dict] = [{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}]
    if suffix:
        # Примеры меняются от отказа к отказу, поэтому идут после точки кэша:
        # новый пример не должен обнулять кэш стабильной части промпта.
        system.append({"type": "text", "text": suffix})

    resp = await _anthropic().beta.messages.create(
        model=settings.model_analyze,
        max_tokens=16000,
        # Если модель откажется разбирать пачку по политике, сервер перезапустит
        # запрос на резервной модели — сообщения не останутся без разбора.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": settings.effort_analyze,
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
        # Отказала вся цепочка, включая резервную модель. Повтор не поможет:
        # сообщения помечаются разобранными без предложений, а в логе остаётся
        # категория отказа.
        log.warning("модель отказалась разбирать пачку: %s", resp.stop_details)
        return [], usage, resp.model
    if resp.stop_reason == "max_tokens":
        log.warning("ответ модели обрезан по max_tokens — пачка будет разобрана заново")
        return None, usage, resp.model

    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.error("ответ модели не разобрался как JSON: %.300s", text)
        return None, usage, resp.model
    return list(data.get("results", [])), usage, resp.model


# --------------------------------------------------------------------------
# Запись результатов
# --------------------------------------------------------------------------


def _clamp(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _upsert(session: AsyncSession, existing: dict[int, Proposal], msg: Message, fields: dict) -> None:
    """Одно предложение на сообщение.

    Сообщение, возвращённое в разбор (задачу отменили или объединили), уже
    имеет предложение. Второе нарушило бы уникальность — поэтому старое
    переписывается на месте.
    """
    p = existing.get(msg.id)
    if p is None:
        p = Proposal(message_id=msg.id, **fields)
        session.add(p)
        existing[msg.id] = p
    else:
        for k, v in fields.items():
            setattr(p, k, v)


def _noise_fields(reading: str, model: str, decided_by: str, confidence: float) -> dict:
    return dict(
        kind=ProposalKind.noise,
        state=ProposalState.rejected,
        task_id=None,
        link_basis="",
        reading=reading,
        action="",
        draft={},
        confidence=confidence,
        model=model,
        decided_at=None,
        decided_by=decided_by,
    )


async def apply_results(
    session: AsyncSession,
    msgs: list[Message],
    results: list[dict],
    tasks_by_ref: dict[str, Task],
    model: str,
    now: datetime,
) -> dict:
    by_id: dict[int, dict] = {}
    for r in results:
        try:
            by_id[int(r["message_id"])] = r
        except (KeyError, TypeError, ValueError):
            continue

    existing = {
        p.message_id: p
        for p in (
            await session.execute(
                select(Proposal).where(Proposal.message_id.in_([m.id for m in msgs]))
            )
        ).scalars()
    }
    stats = {"proposals": 0, "noise": 0, "skipped": 0, "unverified_links": 0}

    for m in msgs:
        m.analyzed_at = now
        r = by_id.get(m.id)
        if r is None:
            # Модель пропустила сообщение. Выдумывать за неё исход нельзя —
            # считаем и пишем в лог, чтобы пропуски были видны.
            stats["skipped"] += 1
            continue
        try:
            kind = ProposalKind(r.get("kind"))
        except ValueError:
            stats["skipped"] += 1
            continue

        confidence = _clamp(r.get("confidence"))
        reading = str(r.get("reading") or "").strip()
        action = str(r.get("action") or "").strip()
        task = tasks_by_ref.get(str(r.get("task_ref") or ""))
        basis = str(r.get("link_basis") or "")

        if kind in CONTINUATIONS and task is None:
            # Модель сослалась на задачу, которой нет среди открытых. Ссылке не
            # верим: лучше лишняя карточка нового запроса, чем правка чужой
            # задачи по выдуманной связи.
            kind = ProposalKind.new_task
            confidence *= 0.5
            reading = f"{reading} Связь с задачей не подтвердилась — показано как новый запрос.".strip()
            stats["unverified_links"] += 1

        if kind == ProposalKind.noise:
            _upsert(session, existing, m, _noise_fields(reading, model, "модель", confidence))
            stats["noise"] += 1
            continue

        d = r.get("draft") or {}
        draft: dict = {}
        if kind == ProposalKind.new_task:
            draft = {
                "title": str(d.get("title") or "")[:300],
                "dod": str(d.get("dod") or ""),
                "type": d.get("type") or TaskType.request.value,
                "module": d.get("module") or Module.unknown.value,
            }
        elif kind == ProposalKind.deadline and d.get("due_at"):
            draft = {"due_at": str(d["due_at"])}
        elif kind == ProposalKind.scope and d.get("dod_add"):
            draft = {"dod_add": str(d["dod_add"])}

        linked = task is not None and kind in CONTINUATIONS
        _upsert(
            session,
            existing,
            m,
            dict(
                kind=kind,
                state=ProposalState.pending,
                task_id=task.id if linked else None,
                link_basis=basis if linked else "",
                reading=reading,
                action=action,
                draft=draft,
                confidence=confidence,
                model=model,
                decided_at=None,
                decided_by="",
            ),
        )
        stats["proposals"] += 1

    if stats["skipped"]:
        log.warning("модель пропустила %d сообщений из %d", stats["skipped"], len(msgs))
    return stats


# --------------------------------------------------------------------------
# Проход
# --------------------------------------------------------------------------


async def analyze_pending(session: AsyncSession, now: datetime | None = None) -> dict:
    """Разбирает неразобранные сообщения включённых чатов. Возвращает статистику."""
    now = now or _now()
    stats = {
        "messages": 0, "prefiltered": 0, "proposals": 0, "noise": 0,
        "skipped": 0, "unverified_links": 0, "calls": 0,
    }

    rows = (
        await session.execute(
            select(Message)
            .join(Chat)
            .options(selectinload(Message.chat))
            .where(Message.analyzed_at.is_(None), Chat.enabled.is_(True))
            # Свежее — вперёд: догруженная история не должна держать в
            # очереди сегодняшние обращения. Внутри каждой части — по времени,
            # чтобы продолжения шли после своих задач.
            .order_by(
                (Message.sent_at < now - timedelta(hours=settings.fresh_hours)).asc(),
                Message.sent_at,
            )
            .limit(settings.analyze_batch_size * 10)
        )
    ).scalars().all()
    if not rows:
        return stats
    stats["messages"] = len(rows)

    existing = {
        p.message_id: p
        for p in (
            await session.execute(
                select(Proposal).where(Proposal.message_id.in_([m.id for m in rows]))
            )
        ).scalars()
    }
    to_model: list[Message] = []
    for m in rows:
        reason = prefilter_reason(m) if settings.prefilter_enabled else None
        if reason is None:
            to_model.append(m)
            continue
        _upsert(session, existing, m, _noise_fields(f"Отсеяно без модели: {reason}.", "prefilter", "фильтр", 1.0))
        m.analyzed_at = now
        stats["prefiltered"] += 1

    if not to_model:
        await session.commit()
        return stats
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY не задан: %d сообщений ждут разбора", len(to_model))
        await session.commit()
        return stats

    # Все открытые задачи, а не только этого чата: «то же самое» чаще всего
    # приходит как раз из другого чата.
    tasks = (
        await session.execute(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(Task.status.in_(OPEN_STATUSES))
            .order_by(Task.last_moved_at.desc())
            .limit(80)
        )
    ).scalars().all()
    tasks_by_ref = {t.ref: t for t in tasks}

    # Какие сообщения уже привязаны к задачам: реплай на такое сообщение —
    # самая надёжная связь, и модели её надо показать явно.
    linked_rows = (
        await session.execute(
            select(Message.chat_id, Message.tg_msg_id, Task.ref)
            .join(ThreadLink, ThreadLink.message_id == Message.id)
            .join(Task, Task.id == ThreadLink.task_id)
            .where(Task.status.in_(OPEN_STATUSES))
        )
    ).all()

    suffix = await feedback_examples(session)
    # Счётчики нулями явно: default=0 срабатывает только при вставке в БД, а до
    # первого commit атрибут None — и += на нём падает.
    run = AgentRun(
        agent="analyze", model=settings.model_analyze, started_at=now,
        input_tokens=0, output_tokens=0, cache_read_tokens=0, items_processed=0,
    )
    session.add(run)

    by_chat: dict[int, list[Message]] = {}
    for m in to_model:
        by_chat.setdefault(m.chat_id, []).append(m)

    try:
        for chat_id, msgs in by_chat.items():
            links = {tg: ref for cid, tg, ref in linked_rows if cid == chat_id}
            for i in range(0, len(msgs), settings.analyze_batch_size):
                batch = msgs[i : i + settings.analyze_batch_size]
                payload = build_input(batch[0].chat.title, batch, list(tasks), links, now)
                results, usage, model = await classify(payload, suffix)
                stats["calls"] += 1
                run.input_tokens += usage["input"]
                run.output_tokens += usage["output"]
                run.cache_read_tokens += usage["cache_read"]
                if results is None:
                    continue
                s = await apply_results(session, batch, results, tasks_by_ref, model, now)
                for k, v in s.items():
                    stats[k] += v
                run.items_processed += len(batch)
                await session.commit()
    except anthropic.RateLimitError as exc:
        run.ok = False
        run.error = f"лимит запросов: {exc}"[:4000]
        log.warning("разбор остановлен лимитом запросов, продолжится на следующем проходе")
    except anthropic.APIStatusError as exc:
        run.ok = False
        run.error = f"HTTP {exc.status_code}: {exc.message}"[:4000]
        log.error("разбор остановлен ошибкой API: %s", run.error)
    except anthropic.APIConnectionError as exc:
        run.ok = False
        run.error = f"нет связи с API: {exc}"[:4000]
        log.warning("разбор остановлен: нет связи с API")

    run.finished_at = _now()
    await session.commit()
    return stats
