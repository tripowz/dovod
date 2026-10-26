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
    TaskType,
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
    # Баг нельзя сдать на проверку или принять без срока. Решение (критерий
    # готовности) у задачи обязательно всегда; для бага к нему добавляется срок:
    # «починим когда-нибудь» — это не закрытый баг, а забытый.
    if (
        task.type == TaskType.bug
        and new in (TaskStatus.review, TaskStatus.done)
        and task.due_at is None
    ):
        raise RuleViolation(
            "У бага не указан срок. Поставьте срок устранения, прежде чем "
            "сдавать его на проверку или принимать."
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


def return_to_rework(task: Task, actor: Staff, reason: str, now: datetime) -> None:
    """Возврат задачи с проверки обратно в работу — отрицательный исход приёмки.

    Приёмка задачи — право автора; вернуть её вправе автор или руководитель:
    проверка со стороны руководителя и есть смысл возврата. Возврат без причины
    запрещён: по причине исполнитель понимает, что доделать, а агент учится на
    том, почему работа не прошла, — пустой возврат теряет и то, и другое.

    Статус уходит в «В работе» тем же событием, что и обычный старт
    («Статус изменён на „В работе"»), поэтому метрика возвратов (которая считает
    повторные входы в работу из истории) засчитывает его автоматически.
    """
    if task.status != TaskStatus.review:
        raise RuleViolation(
            "Вернуть на доработку можно только задачу, сданную на проверку."
        )
    if task.author_id and actor.id != task.author_id and not actor.is_manager:
        raise RuleViolation(
            "Вернуть на доработку может автор задачи или руководитель."
        )
    reason = (reason or "").strip()
    if not reason:
        raise RuleViolation(
            "Укажите причину возврата: что именно доделать и почему работа не принята."
        )
    task.status = TaskStatus.in_progress
    task.last_moved_at = now
    clear_pending(task)
    add_event(task, _status_event_text(TaskStatus.in_progress), actor.name)
    add_event(task, f"Вернул на доработку — {actor.name}: {reason}", actor.name)


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


def close_ticket(ticket: Ticket, now: datetime, enforce_reply: bool = True) -> None:
    """Закрывает заявку. Пока правило включено, без ответа клиенту закрыть нельзя.

    Это то самое «закрыто без ответа»: задача сделана, отель об этом не узнал,
    и для него проблема продолжается. Проверкой управляет правило
    `close_without_reply`: оно самое полезное и самое раздражающее, поэтому
    на первой неделе пилота выключено и включается на второй.
    """
    if enforce_reply and awaiting_client_reply(ticket):
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
        # Пробел как разделитель тысяч ставится только в числе. Раньше замена
        # запятых шла по всей строке и съедала пунктуацию в самом выводе.
        if self.margin > 0:
            return f"Остаётся {_sum(self.margin)} сум в месяц. Вложиться в удержание есть из чего."
        return (
            f"Обслуживание съедает тариф и ещё {_sum(-self.margin)} сум сверху. "
            "Дело не в том, чтобы удержать любой ценой, а в том, чтобы починить причину."
        )


def _sum(n: int) -> str:
    return f"{n:,}".replace(",", " ")


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
# Метрики по сотрудникам
# --------------------------------------------------------------------------
#
# Утренняя сводка отвечает на вопрос «что горит сейчас». Метрики отвечают на
# другой вопрос — «кто как работал за период», и смешивать их нельзя: по
# мгновенной нагрузке нельзя спросить с человека, а по накопленным цифрам —
# можно. Поэтому это отдельная функция, а не поле в `Digest`.
#
# Все показатели считаются из фактов, а не из оценок: приоритет и estimate
# человек ставит сам, а время ответа, просрочки и возвраты на доработку
# подделать нельзя — именно они и есть предмет разговора с сотрудником.


#: Текст события смены статуса. Строится из тех же констант, что и в
#: `change_status`, чтобы подсчёт возвратов не разошёлся с тем, что реально
#: пишется в историю задачи.
def _status_event_text(status: TaskStatus) -> str:
    return f"Статус изменён на «{STATUS_TITLE[status]}»"


@dataclass
class StaffMetrics:
    """Что сделал один сотрудник за период.

    `reworks` — возвраты с «На проверке» обратно в работу. Это самый честный
    индикатор качества: задача, которую автор вернул на доработку, была сдана
    недоделанной. Он считается из истории, а не со слов исполнителя.

    `avg_first_reply_min` равен None, а не нулю, когда клиентам за период не
    отвечали вовсе: ноль читался бы как «отвечали мгновенно» и хвалил бы за
    бездействие.
    """

    staff_id: int
    name: str
    done: int  # задач принято автором (status=done) за период
    resolved_tickets: int  # заявок клиентов закрыто
    avg_first_reply_min: int | None  # среднее время первого ответа клиенту
    reworks: int  # возвраты на доработку
    overdue_now: int  # открытых задач с прошедшим сроком прямо сейчас
    open_now: int  # открытых задач сейчас
    review_now: int  # сдано на приёмку, ждёт автора
    spent_hours: float  # списано часов на закрытых за период задачах
    estimate_hours: float  # оценка тех же задач

    @property
    def rework_rate(self) -> float:
        """Доля возвратов среди доведённого. 0.3 — каждая третья возвращалась."""
        base = self.done + self.reworks
        return round(self.reworks / base, 2) if base else 0.0

    @property
    def estimate_gap_hours(self) -> float:
        """Насколько факт разошёлся с оценкой. Плюс — работа дольше обещанного."""
        return round(self.spent_hours - self.estimate_hours, 1)


def _task_reworks_in_period(task: Task, since: datetime, until: datetime) -> int:
    """Сколько раз задачу возвращали в работу в окне [since, until).

    Возврат — это второй и последующий переход в «В работе»: первый запуск
    задачи нормален, а каждый следующий означает, что её уже сдавали и вернули.
    Окно применяется к самому возврату, а не к первому запуску: задача могла
    стартовать месяц назад, а провалить приёмку на этой неделе. Верхняя граница
    `until` нужна, чтобы считать прошлый период, не захватывая текущий.
    """
    marker = _status_event_text(TaskStatus.in_progress)
    seen_work = False
    count = 0
    for ev in sorted(task.events, key=lambda e: e.created_at):
        if ev.pending or ev.text != marker:
            continue
        if seen_work and since <= ev.created_at < until:
            count += 1
        seen_work = True
    return count


def build_staff_report(
    staff_list: list[Staff],
    tasks: list[Task],
    tickets: list[Ticket],
    now: datetime,
    since: datetime,
    until: datetime | None = None,
) -> list[StaffMetrics]:
    """Собирает метрики по каждому активному сотруднику за окно [since, until).

    Задача засчитывается тому, кто её вёл (assignee), а не тому, кто принял:
    приёмка — обязанность автора, а работу делал исполнитель, и спрашивать за
    неё надо с него.

    `until` по умолчанию равен `now` — обычный отчёт «за последние N дней». Явная
    верхняя граница нужна только для сравнения с прошлым периодом: иначе прошлое
    окно захватило бы и текущее, и динамика всегда выглядела бы нулевой.
    Мгновенные показатели (открыто/просрочено/на проверке) всегда считаются на
    `now` и для прошлого окна не имеют смысла — их там не читают.
    """
    end = until or now
    acc: dict[int, StaffMetrics] = {
        s.id: StaffMetrics(
            staff_id=s.id,
            name=s.name,
            done=0,
            resolved_tickets=0,
            avg_first_reply_min=None,
            reworks=0,
            overdue_now=0,
            open_now=0,
            review_now=0,
            spent_hours=0.0,
            estimate_hours=0.0,
        )
        for s in staff_list
    }
    reply_minutes: dict[int, list[int]] = {s.id: [] for s in staff_list}

    for t in tasks:
        if t.assignee_id not in acc:
            continue
        m = acc[t.assignee_id]
        if t.is_open:
            m.open_now += 1
            if t.is_overdue(now):
                m.overdue_now += 1
            if t.status == TaskStatus.review:
                m.review_now += 1
        elif t.status == TaskStatus.done and since <= t.last_moved_at < end:
            m.done += 1
            m.spent_hours += t.spent_hours or 0.0
            m.estimate_hours += t.estimate_hours or 0.0
        m.reworks += _task_reworks_in_period(t, since, end)

    for tk in tickets:
        if tk.assignee_id not in acc:
            continue
        m = acc[tk.assignee_id]
        if tk.resolved_at and since <= tk.resolved_at < end:
            m.resolved_tickets += 1
        if tk.first_reply_at and since <= tk.first_reply_at < end:
            delta = int((tk.first_reply_at - tk.created_at).total_seconds() // 60)
            reply_minutes[tk.assignee_id].append(max(delta, 0))

    for sid, mins in reply_minutes.items():
        if mins:
            acc[sid].avg_first_reply_min = round(sum(mins) / len(mins))

    # Вперёд те, с кем есть о чём говорить: возвраты, потом просрочки.
    return sorted(
        acc.values(), key=lambda m: (-m.reworks, -m.overdue_now, -m.done)
    )


@dataclass
class StaffTrend:
    """Метрики сотрудника с оглядкой на прошлый такой же период.

    Снимок говорит, как сейчас; динамика — стало лучше или хуже. Для разговора
    важнее второе: две просрочки, которых на прошлой неделе было пять, — это не
    то же самое, что две после нуля. `prev` может быть None (сотрудник появился
    недавно, сравнивать не с чем), и тогда дельты не показываются.
    """

    cur: StaffMetrics
    prev: StaffMetrics | None = None

    @property
    def done_delta(self) -> int:
        return self.cur.done - (self.prev.done if self.prev else 0)

    @property
    def resolved_delta(self) -> int:
        return self.cur.resolved_tickets - (self.prev.resolved_tickets if self.prev else 0)

    @property
    def reworks_delta(self) -> int:
        return self.cur.reworks - (self.prev.reworks if self.prev else 0)

    @property
    def reply_delta(self) -> int | None:
        """Изменение времени ответа. None, если сравнивать не с чем: ноль здесь
        соврал бы, что время не изменилось, хотя его просто не с чем сверить."""
        if (
            self.prev
            and self.cur.avg_first_reply_min is not None
            and self.prev.avg_first_reply_min is not None
        ):
            return self.cur.avg_first_reply_min - self.prev.avg_first_reply_min
        return None


def build_staff_report_trend(
    staff_list: list[Staff],
    tasks: list[Task],
    tickets: list[Ticket],
    now: datetime,
    days: int,
) -> list[StaffTrend]:
    """Отчёт за последние `days` дней рядом с предыдущим таким же окном.

    Предыдущее окно — [now-2·days, now-days): ровно той же длины и впритык, иначе
    сравнение «за неделю» с «за три дня» давало бы ложную динамику.
    """
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    cur = build_staff_report(staff_list, tasks, tickets, now, since)
    prev = {
        m.staff_id: m
        for m in build_staff_report(staff_list, tasks, tickets, now, prev_since, until=since)
    }
    return [StaffTrend(cur=c, prev=prev.get(c.staff_id)) for c in cur]


def _arrow(delta: int) -> str:
    """Направленная стрелка динамики для текста отчёта. Пусто, если без
    изменений. Хорошо это или плохо, в сообщении не раскрашиваешь — цифра рядом
    со стрелкой говорит сама."""
    if delta == 0:
        return ""
    return (" ↑" if delta > 0 else " ↓") + f"{abs(delta)}"


def staff_report_text(rows: list[StaffTrend], days: int) -> str:
    """Недельный отчёт одним сообщением в Telegram.

    Руководитель должен увидеть, с кем разговаривать, не открывая мини-апп:
    первым идёт тот, у кого возвраты и просрочки, а не тот, кто больше закрыл.
    Рядом с возвратами — динамика к прошлой неделе: растут они или спадают,
    важнее их сегодняшнего числа.
    """
    lines = [f"*Отчёт за {days} дн.*"]
    if not rows:
        lines.append("Сотрудников нет.")
        return "\n".join(lines)
    for tr in rows:
        m = tr.cur
        reply = f"{m.avg_first_reply_min} мин" if m.avg_first_reply_min is not None else "—"
        tail = []
        if m.reworks:
            tail.append(f"возвратов {m.reworks}{_arrow(tr.reworks_delta)}")
        if m.overdue_now:
            tail.append(f"просрочено {m.overdue_now}")
        extra = f" ({', '.join(tail)})" if tail else ""
        lines.append(
            f"{m.name}: закрыто {m.done}{_arrow(tr.done_delta)}, "
            f"заявок {m.resolved_tickets}, ответ {reply}{extra}"
        )
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
