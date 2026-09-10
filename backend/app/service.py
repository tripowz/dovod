"""Правила предметной области.

Здесь живёт то, что нельзя доверить ни модели, ни фронтенду: кто имеет право
закрыть задачу, что считается ответом клиенту, как читается история и из чего
складывается утренняя сводка. Всё это проверяется тестами и не дублируется
в API — иначе правило начинает существовать в двух местах и расходится.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import settings
from .models import (
    Escalation,
    Hotel,
    Proposal,
    ProposalState,
    Staff,
    Task,
    TaskEvent,
    TaskStatus,
    Ticket,
    TicketStatus,
)


class RuleViolation(Exception):
    """Действие запрещено правилом, а не техническим сбоем.

    Отдельный тип, чтобы API отвечал 409 с внятным текстом: «нельзя» и
    «сломалось» — разные ответы, и путать их значит учить людей игнорировать
    ошибки.
    """


# --------------------------------------------------------------------------
# История задачи
# --------------------------------------------------------------------------


def add_event(task: Task, text: str, actor: str = "", pending: bool = False) -> None:
    """Добавляет запись в историю, удерживая ожидания в конце.

    Записи-ожидания («ждёт приёмки») не события, а состояние. Если положить
    новое событие после них, лента читается задом наперёд: незавершённый шаг
    оказывается выше того, что случилось позже.
    """
    if pending:
        task.events = [e for e in task.events if not e.pending]
        task.events.append(TaskEvent(text=text, actor_name=actor, pending=True))
        return
    tail: list[TaskEvent] = []
    while task.events and task.events[-1].pending:
        tail.append(task.events.pop())
    task.events.append(TaskEvent(text=text, actor_name=actor, pending=False))
    task.events.extend(reversed(tail))


def clear_pending(task: Task) -> None:
    task.events = [e for e in task.events if not e.pending]


# --------------------------------------------------------------------------
# Статусы задач
# --------------------------------------------------------------------------

#: Что означает статус для одной задачи. Отличается от названия колонки на
#: доске: колонка «Новые» — про много задач, статус «Новая» — про одну.
STATUS_TITLE = {
    TaskStatus.new: "Новая",
    TaskStatus.in_progress: "В работе",
    TaskStatus.review: "На проверке",
    TaskStatus.done: "Готово",
    TaskStatus.merged: "Дубль",
    TaskStatus.cancelled: "Отменена",
}


def change_status(task: Task, new: TaskStatus, actor: Staff, now: datetime) -> None:
    """Меняет статус задачи, соблюдая единственное жёсткое правило.

    `done` ставит только автор задачи. Это главный рычаг против «сделал и
    забыл»: между «я закончил» и «задача решена» помещается большая часть
    потерянной работы, и закрывать её должен тот, кто ставил.

    Руководителю право не выдаётся: иначе правило превращается в
    рекомендацию, а закрывать чужие задачи начинают все.
    """
    if new == task.status:
        return
    if new == TaskStatus.done and task.author_id and actor.id != task.author_id:
        author = task.author.name if task.author else "автор"
        raise RuleViolation(
            f"Принять работу может только автор задачи ({author}). "
            "Вы можете сдать её на проверку."
        )
    if new in (TaskStatus.merged, TaskStatus.cancelled):
        raise RuleViolation(
            "Дубль и отмена оформляются отдельными действиями — "
            "они переносят сообщения обратно в разбор."
        )

    task.status = new
    task.last_moved_at = now
    add_event(task, f"Статус изменён на «{STATUS_TITLE[new]}»", actor.name)
    if new == TaskStatus.review:
        author = task.author.name if task.author else "автора"
        add_event(task, f"Ждёт приёмки — {author}", pending=True)
    elif new == TaskStatus.new:
        add_event(task, "Ждёт начала работы", pending=True)
    else:
        clear_pending(task)


# --------------------------------------------------------------------------
# Заявки клиентов
# --------------------------------------------------------------------------


def sla_left_minutes(ticket: Ticket, now: datetime) -> int | None:
    """Сколько минут осталось до нарушения обещания. None — обещание закрыто.

    Пока клиенту не ответили, считается срок первого ответа, даже если вопрос
    уже передан в разработку. Для отеля проблема жива, пока ему не написали, —
    и «ждём разработку» не должно останавливать этот таймер.
    """
    if ticket.status in (TicketStatus.resolved, TicketStatus.closed):
        return None
    if ticket.first_reply_at is None:
        deadline = ticket.created_at + timedelta(
            minutes=settings.sla_first_reply_minutes
        )
    else:
        deadline = ticket.created_at + timedelta(minutes=settings.sla_resolve_minutes)
    return int((deadline - now).total_seconds() // 60)


def awaiting_client_reply(ticket: Ticket) -> bool:
    """Клиенту так и не ответили. Передача в разработку сюда не считается."""
    return ticket.first_reply_at is None and ticket.status not in (
        TicketStatus.resolved,
        TicketStatus.closed,
    )


def mark_replied(ticket: Ticket, staff: Staff, now: datetime) -> None:
    if ticket.first_reply_at is None:
        ticket.first_reply_at = now
    ticket.assignee_id = staff.id
    if ticket.status == TicketStatus.new:
        ticket.status = TicketStatus.working


def close_ticket(ticket: Ticket, now: datetime) -> None:
    """Закрывает заявку. Без ответа клиенту закрыть нельзя.

    Это то самое «закрыто без ответа»: задача сделана, отель об этом не узнал,
    и для него проблема продолжается. Правило включается на второй неделе
    пилота — оно самое полезное и самое раздражающее.
    """
    if awaiting_client_reply(ticket):
        raise RuleViolation(
            "Клиенту ещё не ответили. Закрыть заявку, о решении которой отель "
            "не знает, значит закрыть её только для себя."
        )
    ticket.status = TicketStatus.resolved
    ticket.resolved_at = now


# --------------------------------------------------------------------------
# Экономика клиента
# --------------------------------------------------------------------------


@dataclass
class HotelEconomics:
    mrr: int
    cost: int
    margin: int

    @property
    def verdict(self) -> str:
        if self.margin > 0:
            return (
                f"Остаётся {self.margin:,} сум в месяц. "
                "Вложиться в удержание есть из чего.".replace(",", " ")
            )
        return (
            f"Обслуживание съедает тариф и ещё {-self.margin:,} сум сверху. "
            "Дело не в том, чтобы удержать любой ценой, а в том, чтобы "
            "починить причину.".replace(",", " ")
        )


def hotel_economics(hotel: Hotel) -> HotelEconomics:
    cost = int(hotel.service_hours * settings.service_hour_cost)
    return HotelEconomics(mrr=hotel.mrr, cost=cost, margin=hotel.mrr - cost)


# --------------------------------------------------------------------------
# Утренняя сводка
# --------------------------------------------------------------------------


@dataclass
class FireItem:
    kind: str  # task | ticket
    ref: str
    title: str
    detail: str
    severity: str  # bad | warn


@dataclass
class LoadItem:
    name: str
    open: int
    overdue: int
    idle: int
    review: int
    limit: int

    @property
    def overloaded(self) -> bool:
        return self.open >= self.limit


@dataclass
class Digest:
    fire: list[FireItem] = field(default_factory=list)
    load: list[LoadItem] = field(default_factory=list)
    at_risk: list[dict] = field(default_factory=list)

    @property
    def overloaded(self) -> list[LoadItem]:
        return [w for w in self.load if w.overloaded]

    @property
    def slipping(self) -> list[LoadItem]:
        """Срывающие срок — это не то же самое, что перегруженные.

        Человек с одной просроченной задачей не перегружен, и лечится это
        разговором, а не разгрузкой. Смешивать их — значит давать неверный
        диагноз.
        """
        return [w for w in self.load if w.overdue]


def build_digest(
    tasks: list[Task], tickets: list[Ticket], hotels: list[Hotel], me: Staff, now: datetime
) -> Digest:
    """Собирает сводку по текущему состоянию, а не по вчерашнему отчёту."""
    d = Digest()

    for t in tickets:
        if not awaiting_client_reply(t):
            continue
        left = sla_left_minutes(t, now)
        if left is not None and left <= settings.sla_first_reply_minutes:
            hotel = t.hotel.name if t.hotel else "клиент"
            d.fire.append(
                FireItem(
                    "ticket",
                    t.ref,
                    f"Клиенту не ответили: {hotel}",
                    f"осталось {left} мин из обещанных {settings.sla_first_reply_minutes}"
                    if left >= 0
                    else f"обещание нарушено {-left} мин назад",
                    "bad",
                )
            )

    by_person: dict[str, LoadItem] = {}
    for t in tasks:
        if not t.is_open:
            continue
        idle = t.idle_days(now)
        if t.is_overdue(now):
            who = t.assignee.name if t.assignee else "никто"
            d.fire.append(
                FireItem("task", t.ref, t.title, f"срок прошёл — {who}", "bad")
            )
        elif t.status == TaskStatus.review and t.author_id == me.id:
            who = t.assignee.name if t.assignee else "никто"
            d.fire.append(
                FireItem("task", t.ref, t.title, f"ждёт вашей приёмки — {who}", "warn")
            )
        elif idle >= settings.escalate_idle_days - 1:
            who = t.assignee.name if t.assignee else "никто"
            d.fire.append(
                FireItem("task", t.ref, t.title, f"{idle} дн. без движения — {who}", "warn")
            )

        if t.assignee:
            w = by_person.setdefault(
                t.assignee.name,
                LoadItem(t.assignee.name, 0, 0, 0, 0, t.assignee.wip_limit),
            )
            w.open += 1
            if t.is_overdue(now):
                w.overdue += 1
            if idle >= 3:
                w.idle += 1
            if t.status == TaskStatus.review:
                w.review += 1

    d.load = sorted(by_person.values(), key=lambda w: (-w.overdue * 10 - w.open))

    for h in hotels:
        if h.risk.value == "low":
            continue
        unanswered = sum(
            1 for t in tickets if t.hotel_id == h.id and awaiting_client_reply(t)
        )
        open_tasks = sum(1 for t in tasks if t.hotel_id == h.id and t.is_open)
        econ = hotel_economics(h)
        d.at_risk.append(
            {
                "name": h.name,
                "risk": h.risk.value,
                "mrr": h.mrr,
                "unanswered": unanswered,
                "open_tasks": open_tasks,
                "margin": econ.margin,
                "signal": (h.signals or [""])[0],
            }
        )
    d.at_risk.sort(key=lambda x: (-x["unanswered"], x["margin"]))
    return d


def digest_text(d: Digest) -> str:
    """Та же сводка одним сообщением в Telegram.

    Приложение открывается, чтобы что-то сделать; узнать, что происходит,
    руководитель должен не открывая его.
    """
    lines = [f"*Утро. Горит {len(d.fire)}.*"]
    for i, f in enumerate(d.fire[:3], 1):
        lines.append(f"{i}. {f.title} — {f.detail}")
    if len(d.fire) > 3:
        lines.append(f"…и ещё {len(d.fire) - 3}")

    if d.overloaded:
        who = ", ".join(f"{w.name} — {w.open} задач" for w in d.overloaded)
        lines.append(f"Перегружены: {who}")
    else:
        lines.append("Нагрузка ровная")
    if d.slipping:
        who = ", ".join(f"{w.name} ({w.overdue})" for w in d.slipping)
        lines.append(f"Срывают срок: {who}")

    if d.at_risk:
        top = d.at_risk[0]
        mrr = f"{top['mrr']:,}".replace(",", " ")
        lines.append(f"Риск оттока: {top['name']}, {mrr} сум/мес")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Осиротевшие предложения
# --------------------------------------------------------------------------


def orphan_proposals(proposals: list[Proposal], task_id: int) -> list[Proposal]:
    """Помечает предложения, чья задача исчезла.

    Их нельзя просто удалить: сообщение из чата остаётся не разобранным, и
    молчаливая потеря — ровно та дыра, из-за которой запросы пропадали в чате.
    Вызывающий код возвращает их в очередь новых запросов.
    """
    out = []
    for p in proposals:
        if p.task_id == task_id and p.state == ProposalState.pending:
            p.state = ProposalState.orphaned
            out.append(p)
    return out


def escalation_key(rule_code: str, kind: str, subject_id: int) -> tuple[str, str, int]:
    """Ключ дедупликации эскалаций: одного человека не будят дважды по поводу."""
    return (rule_code, kind, subject_id)


def already_escalated(existing: list[Escalation], key: tuple[str, str, int]) -> bool:
    return any(
        (e.rule_code, e.subject_kind, e.subject_id) == key for e in existing
    )
