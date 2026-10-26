"""HTTP-интерфейс мини-аппа.

Правила предметной области живут в `service.py` и здесь не дублируются:
эндпоинт переводит запрос в вызов правила и переводит `RuleViolation`
в 409 с человеческим текстом. Как только правило появляется в двух местах,
оно начинает расходиться.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import service
from ..auth import current_manager, current_staff
from ..config import settings
from ..db import get_session
from ..agents.diagnose import run_diagnosis
from ..models import (
    Analysis,
    Chat,
    Diagnosis,
    DiagnosisVerdict,
    Hotel,
    Message,
    Module,
    NoteRole,
    Proposal,
    ProposalFeedback,
    ProposalKind,
    ProposalState,
    Rule,
    Staff,
    Task,
    TaskNote,
    TaskStatus,
    TaskType,
    ThreadLink,
    Ticket,
    TicketStatus,
)

router = APIRouter(prefix="/api")


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _guard(exc: service.RuleViolation) -> HTTPException:
    """Нарушение правила — 409, а не 400 и не 500.

    «Нельзя» и «сломалось» должны выглядеть по-разному, иначе люди привыкают
    игнорировать любые ошибки как технические.
    """
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


# --------------------------------------------------------------------------
# Схемы
# --------------------------------------------------------------------------


class TaskOut(BaseModel):
    ref: str
    title: str
    dod: str
    type: str
    module: str
    priority: int
    status: str
    assignee: str | None
    author: str | None
    hotel: str | None
    due_at: datetime | None
    idle_days: int
    overdue: bool
    notes_count: int

    @classmethod
    def of(cls, t: Task, ts: datetime) -> "TaskOut":
        return cls(
            ref=t.ref,
            title=t.title,
            dod=t.definition_of_done,
            type=t.type.value,
            module=t.module.value,
            priority=t.priority,
            status=t.status.value,
            assignee=t.assignee.name if t.assignee else None,
            author=t.author.name if t.author else None,
            hotel=t.hotel.name if t.hotel else None,
            due_at=t.due_at,
            idle_days=t.idle_days(ts),
            overdue=t.is_overdue(ts),
            notes_count=len(t.notes),
        )


class TaskPatch(BaseModel):
    """Правка задачи.

    Заголовок и критерий готовности с минимальной длиной: пустой критерий —
    главная причина, по которой работа не доводится, и пропускать его через
    правку так же нельзя, как через создание.
    """

    title: str | None = Field(default=None, min_length=6, max_length=300)
    dod: str | None = Field(default=None, min_length=9)
    type: TaskType | None = None
    module: Module | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    assignee_id: int | None = None
    hotel_id: int | None = None
    due_at: datetime | None = None


class NoteIn(BaseModel):
    role: NoteRole
    text: str = Field(min_length=3, max_length=4000)


class StatusIn(BaseModel):
    status: TaskStatus


class ReworkIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class VerdictIn(BaseModel):
    verdict: DiagnosisVerdict
    note: str = Field(default="", max_length=2000)


class TaskCreate(BaseModel):
    title: str = Field(min_length=6, max_length=300)
    dod: str = Field(min_length=9)
    type: TaskType
    module: Module
    assignee_id: int
    due_at: datetime
    hotel_id: int | None = None
    priority: int = Field(default=2, ge=1, le=3)
    proposal_id: int | None = None


# --------------------------------------------------------------------------
# Кто я
# --------------------------------------------------------------------------


@router.get("/me")
async def me(staff: Staff = Depends(current_staff)):
    return {
        "id": staff.id,
        "name": staff.name,
        "is_manager": staff.is_manager,
        "wip_limit": staff.wip_limit,
    }


# --------------------------------------------------------------------------
# Входящие: новые запросы и продолжения
# --------------------------------------------------------------------------


@router.get("/inbox")
async def inbox(
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Proposal)
            .options(
                selectinload(Proposal.message).selectinload(Message.chat),
                selectinload(Proposal.task),
            )
            .where(Proposal.state == ProposalState.pending)
            .where(Proposal.confidence >= settings.proposal_min_confidence)
            .order_by(Proposal.created_at.desc())
            .limit(60)
        )
    ).scalars().all()

    scanned = (await session.execute(select(func.count(Message.id)))).scalar() or 0
    noise = (
        await session.execute(
            select(func.count(Proposal.id)).where(Proposal.kind == ProposalKind.noise)
        )
    ).scalar() or 0

    def dump(p: Proposal) -> dict:
        m = p.message
        return {
            "id": p.id,
            "kind": p.kind.value,
            "chat": m.chat.title if m.chat else "",
            "author": m.author_name or "",
            "sent_at": m.sent_at,
            "text": m.text,
            "reading": p.reading,
            "action": p.action,
            "confidence": round(p.confidence, 2),
            "link_basis": p.link_basis,
            "draft": p.draft or {},
            "task_ref": p.task.ref if p.task else None,
            "task_title": p.task.title if p.task else None,
            # Задача исчезла — карточка не должна выглядеть пустой, а кнопка
            # обязана обещать возврат в разбор, а не мнимое применение.
            "orphan": bool(p.task_id and p.task is None),
        }

    new = [dump(p) for p in rows if p.kind == ProposalKind.new_task]
    upd = [dump(p) for p in rows if p.kind not in (ProposalKind.new_task, ProposalKind.noise)]
    return {"new": new, "updates": upd, "scanned": scanned, "filtered": noise}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    note: str = "",
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Отклонение — это данные, а не просто закрытие карточки."""
    p = await session.get(Proposal, proposal_id, options=[selectinload(Proposal.message)])
    if p is None:
        raise HTTPException(404, "Предложение не найдено")
    p.state = ProposalState.rejected
    p.decided_at = now()
    p.decided_by = staff.name
    session.add(
        ProposalFeedback(
            proposal_id=p.id,
            message_text=p.message.text if p.message else "",
            was_kind=p.kind.value,
            note=note,
        )
    )
    await session.commit()
    return {"ok": True, "message": "Отклонено. Бот учтёт это в разборе похожих сообщений."}


# --------------------------------------------------------------------------
# Задачи
# --------------------------------------------------------------------------


async def _next_ref(session: AsyncSession, prefix: str, model) -> str:
    """Следующий человекочитаемый номер.

    Отдельная последовательность на каждый тип: пропуск в нумерации задач
    читается как потерянная задача, поэтому счётчик кандидатов её не трогает.
    """
    last = (
        await session.execute(select(func.count(model.id)))
    ).scalar() or 0
    return f"{prefix}-{100 + last + 1}"


def _task_query():
    return select(Task).options(
        selectinload(Task.assignee),
        selectinload(Task.author),
        selectinload(Task.hotel),
        selectinload(Task.notes),
        selectinload(Task.events),
        selectinload(Task.thread).selectinload(ThreadLink.message),
    )


@router.get("/tasks")
async def list_tasks(
    scope: str = "all",
    module: str | None = None,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    ts = now()
    rows = (await session.execute(_task_query().order_by(Task.last_moved_at.desc()))).scalars().all()
    open_rows = [t for t in rows if t.is_open]

    if scope == "mine":
        open_rows = [t for t in open_rows if staff.id in (t.assignee_id, t.author_id)]
    elif scope == "stuck":
        open_rows = [t for t in open_rows if t.idle_days(ts) >= 3]
    elif scope == "review":
        open_rows = [t for t in open_rows if t.status == TaskStatus.review]
    elif scope == "late":
        open_rows = [t for t in open_rows if t.is_overdue(ts)]
    if module:
        open_rows = [t for t in open_rows if t.module.value == module]

    return [TaskOut.of(t, ts).model_dump() for t in open_rows]


@router.get("/tasks/{ref}")
async def get_task(
    ref: str,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    t = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Задача не найдена")
    ts = now()
    return {
        **TaskOut.of(t, ts).model_dump(),
        "estimate_hours": t.estimate_hours,
        "spent_hours": t.spent_hours,
        "can_accept": t.author_id == staff.id,
        "notes": [
            {
                "author": n.author_name,
                "role": n.role.value,
                "text": n.text,
                "created_at": n.created_at,
            }
            for n in t.notes
        ],
        "events": [
            {"text": e.text, "actor": e.actor_name, "pending": e.pending, "at": e.created_at}
            for e in t.events
        ],
        "thread": [
            {
                "author": link.message.author_name,
                "text": link.message.text,
                "tag": link.tag,
                "sent_at": link.message.sent_at,
            }
            for link in t.thread
        ],
    }


@router.post("/tasks", status_code=201)
async def create_task(
    body: TaskCreate,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    ts = now()
    task = Task(
        ref=await _next_ref(session, "SB", Task),
        title=body.title,
        definition_of_done=body.dod,
        type=body.type,
        module=body.module,
        priority=body.priority,
        assignee_id=body.assignee_id,
        author_id=staff.id,
        hotel_id=body.hotel_id,
        due_at=body.due_at,
        last_moved_at=ts,
    )
    task.events = []
    session.add(task)
    await session.flush()

    if body.proposal_id:
        p = await session.get(Proposal, body.proposal_id)
        if p is not None:
            p.state = ProposalState.applied
            p.decided_at = ts
            p.decided_by = staff.name
            p.task_id = task.id
            session.add(ThreadLink(task_id=task.id, message_id=p.message_id, tag="request"))
            service.add_event(task, "Задача создана из сообщения", staff.name)
    if not task.events:
        service.add_event(task, "Задача создана", staff.name)
    service.add_event(task, "Ждёт начала работы", pending=True)

    await session.commit()
    return {"ref": task.ref, "message": f"{task.ref} создана"}


@router.patch("/tasks/{ref}")
async def edit_task(
    ref: str,
    body: TaskPatch,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    t = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Задача не найдена")

    changed: list[str] = []
    mapping = {
        "title": ("формулировку", "title"),
        "dod": ("критерий готовности", "definition_of_done"),
        "type": ("тип", "type"),
        "module": ("часть системы", "module"),
        "priority": ("приоритет", "priority"),
        "assignee_id": ("ответственного", "assignee_id"),
        "hotel_id": ("клиента", "hotel_id"),
        "due_at": ("срок", "due_at"),
    }
    for field, (label, attr) in mapping.items():
        value = getattr(body, field)
        if value is not None and getattr(t, attr) != value:
            setattr(t, attr, value)
            changed.append(label)

    if changed:
        t.last_moved_at = now()
        service.add_event(t, f"Изменил {staff.name}: {', '.join(changed)}", staff.name)
        await session.commit()
    return {"ok": True, "changed": changed}


@router.post("/tasks/{ref}/status")
async def set_status(
    ref: str,
    body: StatusIn,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    t = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Задача не найдена")
    try:
        service.change_status(t, body.status, staff, now())
    except service.RuleViolation as exc:
        raise _guard(exc) from None
    await session.commit()
    return {"ok": True, "status": t.status.value}


@router.post("/tasks/{ref}/rework")
async def return_task_to_rework(
    ref: str,
    body: ReworkIn,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Возврат задачи с проверки на доработку с причиной.

    Отрицательный исход приёмки: решает автор задачи или руководитель. Причина
    обязательна — она и указание исполнителю, и обучающий сигнал агенту.
    """
    t = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Задача не найдена")
    try:
        service.return_to_rework(t, staff, body.reason, now())
    except service.RuleViolation as exc:
        raise _guard(exc) from None
    await session.commit()
    return {"ok": True, "status": t.status.value, "message": f"{t.ref} вернули на доработку"}


@router.post("/tasks/{ref}/notes", status_code=201)
async def add_note(
    ref: str,
    body: NoteIn,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    t = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Задача не найдена")
    if body.role == NoteRole.manager and not staff.is_manager:
        raise HTTPException(403, "Заметку от руководителя может оставить только руководитель")
    session.add(
        TaskNote(task_id=t.id, author_id=staff.id, author_name=staff.name,
                 role=body.role, text=body.text)
    )
    t.last_moved_at = now()
    await session.commit()
    return {"ok": True}


@router.post("/tasks/{ref}/merge")
async def merge_task(
    ref: str,
    into: str,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Объединяет задачу с другой как дубль.

    Нить, заметки и связанные заявки переезжают; предложения, ссылавшиеся на
    исчезнувшую задачу, переводятся на приёмник — иначе они превращаются в
    карточки с пустым заголовком, применение которых рапортует об успехе.
    """
    src = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    dst = (await session.execute(_task_query().where(Task.ref == into))).scalar_one_or_none()
    if src is None or dst is None:
        raise HTTPException(404, "Задача не найдена")
    if src.id == dst.id:
        raise HTTPException(400, "Нельзя объединить задачу саму с собой")

    for link in src.thread:
        session.add(ThreadLink(task_id=dst.id, message_id=link.message_id, tag=link.tag))
    for n in src.notes:
        session.add(TaskNote(task_id=dst.id, author_id=n.author_id,
                             author_name=n.author_name, role=n.role, text=n.text))
    for tk in (await session.execute(select(Ticket).where(Ticket.task_id == src.id))).scalars():
        tk.task_id = dst.id
    for p in (await session.execute(select(Proposal).where(Proposal.task_id == src.id))).scalars():
        p.task_id = dst.id

    src.status = TaskStatus.merged
    src.merged_into_id = dst.id
    dst.last_moved_at = now()
    service.add_event(dst, f"Объединено с {src.ref} — это был дубль", staff.name)
    await session.commit()
    return {"ok": True, "message": f"{src.ref} закрыта как дубль {dst.ref}"}


@router.post("/tasks/{ref}/cancel")
async def cancel_task(
    ref: str,
    reason: str = "",
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Признаёт задачу ошибочно созданной.

    Сообщения из нити возвращаются в разбор новыми запросами. Отмена, стоящая
    потери данных, не используется — и тогда неправильные задачи копятся.
    """
    t = (await session.execute(_task_query().where(Task.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Задача не найдена")

    returned = 0
    for link in t.thread:
        msg = await session.get(Message, link.message_id)
        if msg is not None:
            msg.analyzed_at = None  # уйдёт в разбор заново
            returned += 1
    props = (
        await session.execute(
            select(Proposal).where(Proposal.task_id == t.id,
                                   Proposal.state == ProposalState.pending)
        )
    ).scalars().all()
    for p in props:
        p.state = ProposalState.orphaned
        m = await session.get(Message, p.message_id)
        if m is not None:
            m.analyzed_at = None
            returned += 1

    t.status = TaskStatus.cancelled
    service.add_event(t, f"Отменена как ошибочно созданная. {reason}".strip(), staff.name)
    await session.commit()
    return {
        "ok": True,
        "returned": returned,
        "message": f"{t.ref} отменена. Сообщения из её нити вернулись в разбор ({returned}).",
    }


# --------------------------------------------------------------------------
# Заявки
# --------------------------------------------------------------------------


def _diagnosis_payload(d: Diagnosis | None) -> dict | None:
    if d is None:
        return None
    return {
        "summary": d.summary,
        "probable_cause": d.probable_cause,
        "suggested_step": d.suggested_step,
        "related": d.related or [],
        "suggested_module": d.suggested_module.value,
        "suggested_assignee": d.suggested_assignee.name if d.suggested_assignee else None,
        "confidence": round(d.confidence, 2),
        "model": d.model,
        "verdict": d.verdict.value,
        "verdict_note": d.verdict_note,
        "verdict_by": d.verdict_by,
    }


@router.get("/tickets")
async def list_tickets(
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    ts = now()
    rows = (
        await session.execute(
            select(Ticket)
            .options(selectinload(Ticket.hotel), selectinload(Ticket.analysis))
            .order_by(Ticket.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    # Диагнозы одним запросом: отдельная таблица без связи в модели Ticket,
    # поэтому собираем по ticket_id, а не через relationship.
    diag_by_ticket: dict[int, Diagnosis] = {}
    if rows:
        diag_rows = (
            await session.execute(
                select(Diagnosis)
                .options(selectinload(Diagnosis.suggested_assignee))
                .where(Diagnosis.ticket_id.in_([t.id for t in rows]))
            )
        ).scalars().all()
        diag_by_ticket = {d.ticket_id: d for d in diag_rows if d.ticket_id is not None}
    out = []
    for t in rows:
        a = t.analysis
        out.append({
            "ref": t.ref,
            "hotel": t.hotel.name if t.hotel else "",
            "city": t.hotel.city if t.hotel else "",
            "author": t.author_name,
            "role": t.author_role,
            "text": t.text,
            "type": t.type.value,
            "module": t.module.value,
            "status": t.status.value,
            "sla_left": service.sla_left_minutes(t, ts),
            "awaiting_reply": service.awaiting_client_reply(t),
            "task_ref": None,
            "analysis": None if a is None else {
                "who": a.who, "urgency": a.urgency, "history": a.history,
                "client_risk": a.client_risk, "tone": a.tone,
                "draft_reply": a.draft_reply, "actions": a.actions or [],
                "used_for": a.used_for,
            },
            "diagnosis": _diagnosis_payload(diag_by_ticket.get(t.id)),
        })
    return out


@router.post("/tickets/{ref}/reply")
async def reply_ticket(
    ref: str,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Отмечает, что клиенту ответили.

    Само сообщение отправляет человек в Telegram. Автоответ на письмо «опять
    не работает» читается как отписка и злит сильнее, чем пятнадцать минут
    молчания, — а такие письма приходят от тех, кого ещё можно удержать.
    """
    t = (
        await session.execute(
            select(Ticket).options(selectinload(Ticket.analysis)).where(Ticket.ref == ref)
        )
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Заявка не найдена")
    service.mark_replied(t, staff, now())
    if t.analysis:
        t.analysis.used_for = "ответ отправлен"
    await session.commit()
    return {"ok": True, "message": f"{t.ref}: таймер первого ответа остановлен"}


@router.post("/tickets/{ref}/diagnose")
async def diagnose_ticket(
    ref: str,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Ставит обращению технический диагноз: причина, с чем связано, следующий шаг.

    Агент предлагает, не действует: ничего не назначает и клиенту не пишет —
    это подсказка техменеджеру, которую он принимает или правит сам.
    """
    exists = (
        await session.execute(select(Ticket.id).where(Ticket.ref == ref))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, "Заявка не найдена")
    d = await run_diagnosis(session, ref)
    if d is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Диагноз сейчас не поставить — модель недоступна или не задан ключ. "
            "Попробуйте позже.",
        )
    return {"ok": True, "ref": ref, "diagnosis": _diagnosis_payload(d)}


@router.post("/tickets/{ref}/diagnosis/verdict")
async def diagnosis_verdict(
    ref: str,
    body: VerdictIn,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Оценка диагноза человеком: «верный» или «мимо» с пояснением.

    Это и мерило качества агента, и обучающий сигнал: «мимо» с пояснением
    подмешивается в следующие разборы как пример того, чего не повторять.
    """
    if body.verdict == DiagnosisVerdict.pending:
        raise HTTPException(422, "Вердикт должен быть «верный» или «мимо».")
    if body.verdict == DiagnosisVerdict.wrong and not body.note.strip():
        raise HTTPException(
            422, "Для «мимо» нужно пояснение: что в диагнозе не так и как правильно."
        )
    t = (await session.execute(select(Ticket.id).where(Ticket.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Заявка не найдена")
    d = (
        await session.execute(select(Diagnosis).where(Diagnosis.ticket_id == t))
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "У заявки ещё нет диагноза")
    d.verdict = body.verdict
    d.verdict_note = body.note.strip()
    d.verdict_by = staff.name
    d.verdict_at = now()
    await session.commit()
    return {"ok": True, "verdict": d.verdict.value}


@router.get("/report/agent")
async def report_agent(
    days: int = 30,
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    """Качество агента-диагноста за период: сколько оценили и доля верных.

    Доля считается только от оценённых диагнозов: неоценённые — это не «плохо»,
    а «ещё не проверили», и мешать их с ошибками значило бы занижать качество.
    """
    days = max(1, min(days, 365))
    since = now() - timedelta(days=days)
    rows = (
        await session.execute(
            select(Diagnosis.verdict, func.count(Diagnosis.id))
            .where(Diagnosis.created_at >= since)
            .group_by(Diagnosis.verdict)
        )
    ).all()
    counts = {v.value: n for v, n in rows}
    correct = counts.get("correct", 0)
    wrong = counts.get("wrong", 0)
    rated = correct + wrong
    total = correct + wrong + counts.get("pending", 0)
    return {
        "days": days,
        "total": total,
        "rated": rated,
        "correct": correct,
        "wrong": wrong,
        "pending": counts.get("pending", 0),
        "accuracy": round(correct / rated, 2) if rated else None,
    }


@router.post("/tickets/{ref}/attach")
async def attach_ticket(
    ref: str,
    task_ref: str,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Прикрепляет обращение к существующей задаче вместо создания дубля."""
    t = (
        await session.execute(
            select(Ticket).options(selectinload(Ticket.analysis)).where(Ticket.ref == ref)
        )
    ).scalar_one_or_none()
    task = (await session.execute(_task_query().where(Task.ref == task_ref))).scalar_one_or_none()
    if t is None or task is None:
        raise HTTPException(404, "Заявка или задача не найдена")

    t.task_id = task.id
    t.status = TicketStatus.waiting_dev
    t.assignee_id = staff.id
    if t.analysis:
        t.analysis.used_for = f"прикреплено к {task.ref}, клиенту ещё не ответили"
    if t.message_id:
        session.add(ThreadLink(task_id=task.id, message_id=t.message_id, tag="complaint"))
    task.last_moved_at = now()
    hotel = t.hotel.name if t.hotel else "клиент"
    service.add_event(task, f"Прикреплено обращение {t.ref} — {hotel}", staff.name)
    await session.commit()
    return {
        "ok": True,
        "message": f"{t.ref} прикреплено к {task.ref}. Дубль не создан, "
                   "клиенту всё ещё нужно ответить.",
    }


@router.post("/tickets/{ref}/resolve")
async def resolve_ticket(
    ref: str,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    t = (await session.execute(select(Ticket).where(Ticket.ref == ref))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Заявка не найдена")
    rule = (
        await session.execute(select(Rule).where(Rule.code == "close_without_reply"))
    ).scalar_one_or_none()
    # Правила нет в базе (seed не запускали) — проверяем. Безопасное умолчание
    # лучше тихо выключенной защиты.
    enforce = rule.enabled if rule is not None else True
    try:
        service.close_ticket(t, now(), enforce_reply=enforce)
    except service.RuleViolation as exc:
        raise _guard(exc) from None
    await session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Клиенты
# --------------------------------------------------------------------------


@router.get("/hotels")
async def list_hotels(
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    ts = now()
    hotels = (await session.execute(select(Hotel))).scalars().all()
    tasks = (await session.execute(select(Task))).scalars().all()
    tickets = (await session.execute(select(Ticket))).scalars().all()

    order = {"high": 0, "medium": 1, "low": 2}
    out = []
    for h in hotels:
        econ = service.hotel_economics(h)
        out.append({
            "id": h.id,
            "name": h.name,
            "city": h.city,
            "rooms": h.rooms,
            "tariff": h.tariff,
            "risk": h.risk.value,
            "mrr": h.mrr,
            "cost": econ.cost,
            "margin": econ.margin,
            "verdict": econ.verdict,
            "payment_note": h.payment_note,
            "signals": h.signals or [],
            "offers": h.offers or [],
            # Считается по текущим данным, а не хранится: иначе карточка
            # показывает вчерашнюю картину при сегодняшнем решении.
            "open_tasks": sum(1 for t in tasks if t.hotel_id == h.id and t.is_open),
            "unanswered": sum(
                1 for t in tickets
                if t.hotel_id == h.id and service.awaiting_client_reply(t)
            ),
        })
    out.sort(key=lambda x: (order.get(x["risk"], 3), -x["mrr"]))
    return out


# --------------------------------------------------------------------------
# Сводка и отчёт
# --------------------------------------------------------------------------


@router.get("/digest")
async def digest(
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    ts = now()
    tasks = (
        await session.execute(
            select(Task).options(selectinload(Task.assignee), selectinload(Task.author))
        )
    ).scalars().all()
    tickets = (
        await session.execute(select(Ticket).options(selectinload(Ticket.hotel)))
    ).scalars().all()
    hotels = (await session.execute(select(Hotel))).scalars().all()

    d = service.build_digest(list(tasks), list(tickets), list(hotels), staff, ts)
    return {
        "fire": [f.__dict__ for f in d.fire],
        "load": [
            {**w.__dict__, "overloaded": w.overloaded} for w in d.load
        ],
        "at_risk": d.at_risk,
        "telegram_text": service.digest_text(d),
    }


@router.get("/report/modules")
async def report_modules(
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    since = now() - timedelta(days=7)
    rows = (
        await session.execute(select(Task).where(Task.created_at >= since))
    ).scalars().all()
    agg: dict[str, dict] = {}
    for t in rows:
        a = agg.setdefault(t.module.value, {"module": t.module.value, "all": 0, "bugs": 0, "done": 0})
        a["all"] += 1
        if t.type == TaskType.bug:
            a["bugs"] += 1
        if t.status == TaskStatus.done:
            a["done"] += 1
    return sorted(agg.values(), key=lambda x: -x["all"])


def _staff_metrics_payload(tr: service.StaffTrend) -> dict:
    """Текущие метрики сотрудника плюс динамика к прошлому такому же периоду.

    Дельты отдаём числами, а не «лучше/хуже»: что считать хорошим ростом,
    зависит от показателя (закрыто — вверх хорошо, возвраты — вверх плохо), и
    эту трактовку держит у себя экран, а не сервер.
    """
    m = tr.cur
    return {
        "staff_id": m.staff_id,
        "name": m.name,
        "done": m.done,
        "resolved_tickets": m.resolved_tickets,
        "avg_first_reply_min": m.avg_first_reply_min,
        "reworks": m.reworks,
        "rework_rate": m.rework_rate,
        "overdue_now": m.overdue_now,
        "open_now": m.open_now,
        "review_now": m.review_now,
        "spent_hours": round(m.spent_hours, 1),
        "estimate_hours": round(m.estimate_hours, 1),
        "estimate_gap_hours": m.estimate_gap_hours,
        "trend": {
            "done": tr.done_delta,
            "resolved_tickets": tr.resolved_delta,
            "reworks": tr.reworks_delta,
            "avg_first_reply_min": tr.reply_delta,
            "has_prev": tr.prev is not None,
        },
    }


async def _build_staff_trend(session: AsyncSession, days: int) -> list[service.StaffTrend]:
    """Общий сбор данных для метрик: задачи с историей, заявки, сотрудники.

    История задачи (`Task.events`) грузится сразу: возвраты на доработку
    считаются из неё, а до-загрузка по одной задаче превратила бы отчёт в
    сотни запросов.
    """
    tasks = (
        await session.execute(
            select(Task).options(
                selectinload(Task.assignee), selectinload(Task.events)
            )
        )
    ).scalars().all()
    tickets = (await session.execute(select(Ticket))).scalars().all()
    staff_rows = (
        await session.execute(select(Staff).where(Staff.active.is_(True)))
    ).scalars().all()
    return service.build_staff_report_trend(
        list(staff_rows), list(tasks), list(tickets), now(), days
    )


@router.get("/report/staff")
async def report_staff(
    days: int = 7,
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    """Метрики по каждому сотруднику за период с динамикой. Только руководителю."""
    days = max(1, min(days, 90))
    rows = await _build_staff_trend(session, days)
    return {"days": days, "staff": [_staff_metrics_payload(tr) for tr in rows]}


@router.get("/report/weekly")
async def report_weekly(
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    """Недельный отчёт: те же метрики плюс готовый текст для Telegram."""
    rows = await _build_staff_trend(session, 7)
    return {
        "days": 7,
        "staff": [_staff_metrics_payload(tr) for tr in rows],
        "telegram_text": service.staff_report_text(rows, 7),
    }


# --------------------------------------------------------------------------
# Сотрудники, применение предложений, удержание
# --------------------------------------------------------------------------


@router.get("/staff")
async def list_staff(
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Staff).where(Staff.active.is_(True)).order_by(Staff.name)
        )
    ).scalars().all()
    return [{"id": s.id, "name": s.name, "is_manager": s.is_manager} for s in rows]


_THREAD_TAG = {
    ProposalKind.progress: "resolution",
    ProposalKind.deadline: "postpone",
    ProposalKind.scope: "clarification",
    ProposalKind.duplicate: "complaint",
    ProposalKind.question: "question",
}


@router.post("/proposals/{proposal_id}/apply")
async def apply_proposal(
    proposal_id: int,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Применяет продолжение из чата к задаче.

    Новый запрос сюда не приходит: он оформляется формой (POST /tasks), потому
    что у задачи обязаны появиться ответственный, срок и критерий готовности —
    угадывать их молча модель не должна.

    Если задачи уже нет или она закрыта, ответ честно говорит об этом, а
    сообщение возвращается в разбор. Рапорт об успехе там, где ничего не
    произошло, — худший вид ошибки для системы, которая держится на доверии.
    """
    p = await session.get(Proposal, proposal_id, options=[selectinload(Proposal.message)])
    if p is None:
        raise HTTPException(404, "Предложение не найдено")
    if p.state != ProposalState.pending:
        raise HTTPException(409, "Это предложение уже обработано")
    if p.kind == ProposalKind.new_task:
        raise HTTPException(
            409, "Новый запрос оформляется формой: нужны ответственный, срок и критерий готовности."
        )

    ts = now()
    p.decided_at = ts
    p.decided_by = staff.name
    task = None
    if p.task_id:
        task = (
            await session.execute(_task_query().where(Task.id == p.task_id))
        ).scalar_one_or_none()

    if task is None or not task.is_open:
        p.state = ProposalState.orphaned
        if p.message is not None:
            p.message.analyzed_at = None
        await session.commit()
        why = (
            "Задачи больше нет"
            if task is None
            else f"Задача {task.ref} уже закрыта ({service.STATUS_TITLE[task.status]})"
        )
        return {
            "ok": False,
            "orphan": True,
            "message": f"{why} — применять не к чему. Сообщение вернулось в разбор новым запросом.",
        }

    draft = p.draft or {}
    if p.kind == ProposalKind.progress:
        try:
            service.change_status(task, TaskStatus.review, staff, ts)
        except service.RuleViolation as exc:
            raise _guard(exc) from None
        what = "сдана на проверку"
    elif p.kind == ProposalKind.deadline:
        raw = draft.get("due_at")
        if not raw:
            raise HTTPException(409, "Модель не назвала новый срок — поправьте задачу вручную.")
        due = datetime.fromisoformat(str(raw))
        task.due_at = (
            due.astimezone(timezone.utc).replace(tzinfo=None) if due.tzinfo else due
        )
        service.add_event(
            task, f"Срок перенесён на {task.due_at:%d.%m} — по сообщению в чате", staff.name
        )
        what = "срок перенесён"
    elif p.kind == ProposalKind.scope:
        add = str(draft.get("dod_add") or "").strip()
        if not add:
            raise HTTPException(409, "Модель не сформулировала, что добавить, — поправьте вручную.")
        task.definition_of_done = f"{task.definition_of_done.rstrip()} {add}"
        service.add_event(task, "Критерий готовности дополнен из чата", staff.name)
        what = "критерий готовности дополнен"
    elif p.kind == ProposalKind.duplicate:
        service.add_event(task, "Прикреплено ещё одно сообщение о той же проблеме", staff.name)
        what = "сообщение прикреплено, дубль не создан"
    else:
        service.add_event(task, "Вопрос по задаче добавлен в нить", staff.name)
        what = "вопрос добавлен в нить"

    task.last_moved_at = ts
    already = (
        await session.execute(
            select(ThreadLink).where(
                ThreadLink.task_id == task.id, ThreadLink.message_id == p.message_id
            )
        )
    ).scalar_one_or_none()
    if already is None:
        session.add(
            ThreadLink(task_id=task.id, message_id=p.message_id, tag=_THREAD_TAG.get(p.kind, "request"))
        )
    p.state = ProposalState.applied
    await session.commit()
    return {
        "ok": True,
        "orphan": False,
        "message": f"{task.ref}: {what}. Сообщение осталось в нити задачи.",
    }


@router.post("/hotels/{hotel_id}/offers/{idx}/task", status_code=201)
async def offer_to_task(
    hotel_id: int,
    idx: int,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    """Предложение по удержанию превращается в задачу.

    Ответственный — тот, кто нажал: решение удерживать клиента принимает
    конкретный человек, и он же отвечает за звонок. Срок — завтра: риск
    оттока не ждёт планёрки.
    """
    h = await session.get(Hotel, hotel_id)
    if h is None:
        raise HTTPException(404, "Клиент не найден")
    offers = h.offers or []
    if not 0 <= idx < len(offers):
        raise HTTPException(404, "Предложение не найдено")
    offer = offers[idx]
    title = str(offer.get("h", "")).strip() or "Действие по удержанию клиента"

    ts = now()
    task = Task(
        ref=await _next_ref(session, "SB", Task),
        title=title[:300],
        definition_of_done=f"{title} — сделано, реакция {h.name} записана заметкой в задачу.",
        type=TaskType.request,
        module=Module.unknown,
        priority=3,
        assignee_id=staff.id,
        author_id=staff.id,
        hotel_id=h.id,
        due_at=ts + timedelta(days=1),
        last_moved_at=ts,
    )
    task.events = []
    session.add(task)
    await session.flush()
    service.add_event(task, f"Создана из предложения по удержанию {h.name}", staff.name)
    service.add_event(task, "Ждёт начала работы", pending=True)
    await session.commit()
    return {"ref": task.ref, "message": f"{task.ref} создана и привязана к {h.name}"}


# --------------------------------------------------------------------------
# Источники и правила
# --------------------------------------------------------------------------


@router.get("/chats")
async def list_chats(
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(select(Chat).order_by(Chat.title))).scalars().all()
    return [
        {
            "id": c.id, "title": c.title, "kind": c.kind.value,
            "enabled": c.enabled, "consent_note": c.consent_note,
            # Личная переписка требует не настройки, а согласия. Флаг едет на
            # фронт, чтобы включение показывало предупреждение, а не тумблер.
            "needs_consent": c.kind.value == "private",
        }
        for c in rows
    ]


@router.post("/chats/{chat_id}/toggle")
async def toggle_chat(
    chat_id: int,
    consent_note: str = "",
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    c = await session.get(Chat, chat_id)
    if c is None:
        raise HTTPException(404, "Чат не найден")
    if not c.enabled and c.kind.value == "private" and not consent_note.strip():
        raise HTTPException(
            409,
            "Это личная переписка. Включение требует письменного согласия "
            "сотрудника — укажите, от кого и когда оно получено.",
        )
    c.enabled = not c.enabled
    if c.enabled and consent_note:
        c.consent_note = consent_note
    await session.commit()
    return {"ok": True, "enabled": c.enabled}


@router.get("/rules")
async def list_rules(
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(select(Rule))).scalars().all()
    return [
        {"code": r.code, "title": r.title, "description": r.description,
         "enabled": r.enabled, "hits_7d": r.hits_7d}
        for r in rows
    ]


@router.post("/rules/{code}/toggle")
async def toggle_rule(
    code: str,
    staff: Staff = Depends(current_manager),
    session: AsyncSession = Depends(get_session),
):
    r = (await session.execute(select(Rule).where(Rule.code == code))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "Правило не найдено")
    r.enabled = not r.enabled
    await session.commit()
    return {
        "ok": True,
        "enabled": r.enabled,
        "message": "Правило включено. Сработает без вашего участия."
        if r.enabled
        else "Правило выключено. Нарушение попадёт в отчёт, но никого не разбудит.",
    }
