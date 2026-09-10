"""Тесты правил предметной области.

Проверяется не «код выполнился», а то, что правила действительно защищают:
чужую задачу нельзя закрыть, заявку без ответа клиенту нельзя объявить
решённой, история не переворачивается, перегрузка и срыв срока — разные
диагнозы.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import service  # noqa: E402
from app.models import (  # noqa: E402
    Hotel,
    RiskLevel,
    Staff,
    Task,
    TaskStatus,
    Ticket,
    TicketStatus,
)

NOW = datetime(2026, 9, 10, 9, 0, 0)

bekzod = Staff(id=1, tg_user_id=1, name="Бекзод", is_manager=True, wip_limit=6)
timur = Staff(id=2, tg_user_id=2, name="Тимур", is_manager=False, wip_limit=6)
malika = Staff(id=3, tg_user_id=3, name="Малика", is_manager=False, wip_limit=6)

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


def make_task(**kw) -> Task:
    base = dict(
        id=1,
        ref="SB-138",
        title="Синк тарифов падает по ночам",
        definition_of_done="Три ночи логи чистые",
        status=TaskStatus.in_progress,
        assignee=timur,
        assignee_id=timur.id,
        author=malika,
        author_id=malika.id,
        last_moved_at=NOW,
    )
    base.update(kw)
    t = Task(**base)
    t.events = []
    t.notes = []
    return t


def make_ticket(**kw) -> Ticket:
    base = dict(
        id=1,
        ref="T-319",
        text="Опять бронь не пришла",
        status=TicketStatus.new,
        created_at=NOW - timedelta(minutes=1),
    )
    base.update(kw)
    return Ticket(**base)


# --- приёмка ---------------------------------------------------------------

t = make_task()
try:
    service.change_status(t, TaskStatus.done, timur, NOW)
    check("исполнитель не может закрыть чужую задачу", False, "закрыл")
except service.RuleViolation as e:
    check("исполнитель не может закрыть чужую задачу", "только автор" in str(e), str(e)[:60])

t = make_task()
try:
    service.change_status(t, TaskStatus.done, bekzod, NOW)
    check("руководитель тоже не может закрыть чужую задачу", False, "закрыл")
except service.RuleViolation:
    check("руководитель тоже не может закрыть чужую задачу", True)

t = make_task()
service.change_status(t, TaskStatus.done, malika, NOW)
check("автор закрывает свою задачу", t.status == TaskStatus.done)

t = make_task()
service.change_status(t, TaskStatus.review, timur, NOW)
check("исполнитель может сдать на проверку", t.status == TaskStatus.review)

# --- порядок истории -------------------------------------------------------

t = make_task()
service.change_status(t, TaskStatus.review, timur, NOW)
service.add_event(t, "Изменил Бекзод: формулировку", "Бекзод")
last = t.events[-1]
check(
    "запись-ожидание остаётся в конце истории",
    last.pending and "Ждёт приёмки" in last.text,
    " | ".join(e.text for e in t.events),
)
check(
    "новое событие встаёт перед ожиданием",
    any("Изменил" in e.text for e in t.events[:-1]),
)

# --- пол автора ------------------------------------------------------------

t = make_task()
try:
    service.change_status(t, TaskStatus.done, timur, NOW)
except service.RuleViolation as e:
    msg = str(e)
    check(
        "отказ сформулирован без рода",
        " он " not in msg and " она " not in msg,
        msg[:70],
    )

# --- обещания клиенту ------------------------------------------------------

tk = make_ticket()
left = service.sla_left_minutes(tk, NOW)
check("таймер первого ответа идёт", left is not None and left <= 15, f"{left} мин")

tk = make_ticket(status=TicketStatus.waiting_dev, task_id=5)
check(
    "передача в разработку не считается ответом клиенту",
    service.awaiting_client_reply(tk),
)
left_after = service.sla_left_minutes(tk, NOW)
check(
    "таймер первого ответа не останавливается передачей в разработку",
    left_after is not None and left_after <= 15,
    f"{left_after} мин",
)

tk = make_ticket(status=TicketStatus.waiting_dev)
try:
    service.close_ticket(tk, NOW)
    check("нельзя закрыть заявку без ответа клиенту", False, "закрылась")
except service.RuleViolation as e:
    check("нельзя закрыть заявку без ответа клиенту", "не ответили" in str(e))

tk = make_ticket()
service.mark_replied(tk, bekzod, NOW)
service.close_ticket(tk, NOW)
check("после ответа заявка закрывается", tk.status == TicketStatus.resolved)

# --- экономика клиента -----------------------------------------------------

h = Hotel(id=1, name="Hotel Registon", mrr=1_800_000, service_hours=31, risk=RiskLevel.high)
e = service.hotel_economics(h)
check("обслуживание считается по ставке часа", e.cost == 31 * 60000, f"{e.cost}")
check("убыточный клиент виден по знаку маржи", e.margin < 0, f"{e.margin}")
check("вывод не советует удерживать любой ценой", "починить причину" in e.verdict)

h2 = Hotel(id=2, name="Ichan Qala", mrr=1_250_000, service_hours=9, risk=RiskLevel.medium)
check("прибыльный клиент даёт другой вывод", service.hotel_economics(h2).margin > 0)

# --- сводка ----------------------------------------------------------------

tasks = [
    make_task(id=1, ref="SB-131", assignee=timur, assignee_id=2,
              due_at=NOW - timedelta(days=5), last_moved_at=NOW - timedelta(days=5)),
    make_task(id=2, ref="SB-139", status=TaskStatus.review, author=bekzod, author_id=1,
              assignee=timur, assignee_id=2, last_moved_at=NOW),
    make_task(id=3, ref="SB-140", assignee=malika, assignee_id=3, last_moved_at=NOW),
]
tickets = [make_ticket(hotel_id=1)]
hotels = [h, h2]
d = service.build_digest(tasks, tickets, hotels, bekzod, NOW)

check("просроченная задача попадает в «горит»",
      any(f.ref == "SB-131" and "срок прошёл" in f.detail for f in d.fire))
check("задача на моей приёмке попадает в «горит»",
      any(f.ref == "SB-139" and "приёмки" in f.detail for f in d.fire))
check("необслуженная заявка попадает в «горит»",
      any(f.kind == "ticket" for f in d.fire))
check("клиенты в риске отсортированы по неотвеченным",
      d.at_risk and d.at_risk[0]["name"] == "Hotel Registon",
      str([x["name"] for x in d.at_risk]))

# Тимур: 2 открытых при лимите 6 — не перегружен, но одну просрочил.
check("одна просрочка не делает человека перегруженным",
      not d.overloaded and len(d.slipping) == 1,
      f"перегружено {len(d.overloaded)}, срывают {len(d.slipping)}")

text = service.digest_text(d)
check("в тексте сводки нет ложной перегрузки",
      "Нагрузка ровная" in text and "Срывают срок" in text, text.replace("\n", " | ")[:120])

print()
print("=" * 62)
print(f"ИТОГ: {passed} пройдено, {failed} провалено")
sys.exit(1 if failed else 0)
