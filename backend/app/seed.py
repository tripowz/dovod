"""Начальное наполнение базы.

    python -m app.seed          — руководитель и правила эскалации
    python -m app.seed --demo   — плюс демонстрационные отели, задачи и заявка

Демо-сотрудники получают отрицательные Telegram ID: настоящих таких не
бывает, поэтому войти под ними невозможно, а перепутать их с живыми людьми —
тоже. Демо-данные нужны, чтобы первое открытие приложения показывало, как оно
работает, а не пустые экраны.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import service
from .config import settings
from .db import SessionLocal, init_db
from .models import (
    Analysis,
    Hotel,
    Module,
    RiskLevel,
    Rule,
    Staff,
    Task,
    TaskStatus,
    TaskType,
    Ticket,
    TicketStatus,
)

RULES = [
    (
        "first_reply_late",
        "Первый ответ просрочен",
        "Через 15 минут без ответа — дежурному, через 25 — руководителю.",
        True,
    ),
    (
        "hotel_third_ticket",
        "Третье обращение отеля за неделю",
        "Отель уходит в «риск», руководителю приходит карточка клиента.",
        True,
    ),
    (
        "task_idle",
        "Задача без движения",
        "Напоминание исполнителю на третий день, на пятый — в утреннюю сводку.",
        True,
    ),
    # «Возврат с проверки дважды подряд» вернётся, когда появится учёт
    # возвратов: тумблер, который ничего не делает, хуже отсутствующего правила.
    (
        "close_without_reply",
        "Заявка закрыта без ответа клиенту",
        "Закрыть заявку нельзя, пока клиенту не написали. "
        "Самое полезное и самое раздражающее правило — включать на второй неделе пилота.",
        False,
    ),
]

DEMO_HOTELS = [
    dict(
        name="Hotel Registon", city="Самарканд", rooms=48, tariff="PMS + CM + WEBSITE",
        mrr=1_800_000, service_hours=31, risk=RiskLevel.high,
        payment_note="2 400 000 сум, просрочка 14 дней",
        signals=[
            "Пять обращений за неделю, четыре — про синхронизацию с Ostrovok",
            "Время решения выросло с 3 до 9 часов",
            "Задолженность 14 дней, раньше платили день в день",
        ],
        offers=[
            {"h": "Позвонить владельцу сегодня", "c": "15 минут",
             "p": "Четыре обращения по одной поломке подряд. Разговор на опережение "
                  "стоит ноль, молчание стоит договора."},
            {"h": "Включить мониторинг синка Ostrovok", "c": "4 часа разработки",
             "p": "Уведомлять отель раньше, чем он заметит пропажу брони."},
        ],
    ),
    dict(
        name="Bek Samarkand", city="Самарканд", rooms=26, tariff="PMS + CM",
        mrr=980_000, service_hours=6, risk=RiskLevel.medium,
        payment_note="оплачено до 1 октября",
        signals=["Спорный счёт за август на 1 200 000 сум больше июльского"],
        offers=[{"h": "Прислать расшифровку счёта сегодня", "c": "30 минут",
                 "p": "Вопрос простой, а тянется двое суток. Задержка обиднее суммы."}],
    ),
    dict(
        name="Ichan Qala", city="Хива", rooms=34, tariff="PMS + CM",
        mrr=1_250_000, service_hours=9, risk=RiskLevel.low,
        payment_note="оплачено до 15 сентября",
        signals=["Спрашивают, как подключить Ostrovok — растут, а не жалуются"],
        offers=[{"h": "Предложить WEBSITE и модуль бронирования", "c": "звонок + демо",
                 "p": "Отель сам ищет новые каналы. Момент для расширения тарифа — сейчас."}],
    ),
    dict(
        name="Chorsu Tashkent", city="Ташкент", rooms=62, tariff="PMS + CM + MOBILE",
        mrr=1_400_000, service_hours=3, risk=RiskLevel.medium,
        payment_note="оплачено до 1 октября",
        signals=["Открывают только Календарь, отчёты не открывали ни разу за квартал"],
        offers=[{"h": "Провести обучение по отчётам", "c": "1 час онлайн",
                 "p": "Платят за три модуля, пользуются одним. При продлении посчитают только его."}],
    ),
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _demo(session, manager: Staff | None, now: datetime) -> None:
    if (await session.execute(select(Hotel).limit(1))).scalar_one_or_none() is not None:
        print("[=] отели уже есть — демо-данные не загружаются повторно")
        return

    hotels = {}
    for h in DEMO_HOTELS:
        obj = Hotel(**h)
        session.add(obj)
        hotels[obj.name] = obj

    team = {}
    for i, name in enumerate(("Тимур", "Дилшод", "Азиза"), start=1):
        s = Staff(tg_user_id=-i, name=name)
        session.add(s)
        team[name] = s
    await session.flush()

    author = manager or team["Азиза"]
    demo_tasks = [
        ("Синхронизация тарифов с Booking падает по ночам у трёх отелей",
         "Три ночи подряд логи синка чистые, отели подтвердили, что цены встали.",
         TaskType.bug, Module.cm, 3, "Тимур", "Hotel Registon", TaskStatus.in_progress, 2, 1),
        ("Отчёт по задолженности отелей — выгрузка в Excel",
         "Отчёт открывается в Excel, итоговые суммы сходятся с бухгалтерией.",
         TaskType.request, Module.billing, 2, "Азиза", None, TaskStatus.in_progress, -5, 5),
        ("Вынести OTA-чат из раздела «Ещё» на главный экран",
         "OTA-чат виден на первом экране, за неделю его открыли минимум 20 отелей.",
         TaskType.problem, Module.mobile, 2, "Дилшод", None, TaskStatus.new, 9, 2),
        ("Бронь с Ostrovok не попадает в Календарь",
         "Тестовая бронь Ostrovok видна в Календаре меньше чем за 2 минуты.",
         TaskType.bug, Module.cm, 3, "Дилшод", "Ichan Qala", TaskStatus.review, 1, 0),
    ]
    for n, (title, dod, typ, mod, prio, who, hotel, st, due_days, idle) in enumerate(demo_tasks):
        t = Task(
            ref=f"SB-{101 + n}", title=title, definition_of_done=dod, type=typ, module=mod,
            priority=prio, status=st, assignee_id=team[who].id, author_id=author.id,
            hotel_id=hotels[hotel].id if hotel else None,
            due_at=now + timedelta(days=due_days), last_moved_at=now - timedelta(days=idle),
        )
        t.events = []
        service.add_event(t, "Задача создана (демо)", author.name)
        if st == TaskStatus.review:
            service.add_event(t, f"Ждёт приёмки — {author.name}", pending=True)
        session.add(t)

    await session.flush()
    ticket = Ticket(
        ref="T-101", hotel_id=hotels["Hotel Registon"].id, author_name="Малика",
        author_role="администратор", type=TaskType.bug, module=Module.cm,
        status=TicketStatus.new, created_at=now - timedelta(minutes=2),
        text="Опять! Бронь с Ostrovok на 18 сентября не пришла. Четвёртый раз за неделю. "
             "Гость приехал — а номер продан. Мы так работать не можем.",
    )
    session.add(ticket)
    await session.flush()
    session.add(Analysis(
        ticket_id=ticket.id,
        who="Малика — администратор Hotel Registon",
        urgency="Высокая: гость уже на стойке, номер продан дважды",
        history="Четвёртое обращение по CM за семь дней, все про Ostrovok",
        client_risk="Отель в зоне риска: просрочка 14 дней",
        tone="Раздражение. «Опять», «мы так работать не можем» — предпоследнее письмо "
             "перед разговором о расторжении",
        draft_reply="Малика, вижу проблему — бронь застряла на нашей стороне, не у Ostrovok. "
                    "Номер освобожу вручную, гостя размещайте. Исправление выкатим сегодня. "
                    "Простите за четвёртый раз — это наша вина.",
        actions=[
            "Прикрепить к SB-101 — та же поломка, новую задачу не создавать",
            "Ответить в течение 15 минут",
            "Эскалировать: нужен звонок владельцу, а не переписка",
        ],
        model="demo",
    ))
    print("[+] демо: 4 отеля, 3 сотрудника, 4 задачи, 1 заявка с разбором")


async def main(demo: bool, quiet: bool = False) -> None:
    await init_db()
    now = _now()
    async with SessionLocal() as s:
        manager = None
        if settings.manager_tg_user_id:
            manager = (
                await s.execute(select(Staff).where(Staff.tg_user_id == settings.manager_tg_user_id))
            ).scalar_one_or_none()
            if manager is None:
                manager = Staff(
                    tg_user_id=settings.manager_tg_user_id,
                    name=settings.manager_name,
                    is_manager=True,
                )
                s.add(manager)
                print(f"[+] руководитель добавлен: {manager.name}")
            else:
                manager.is_manager = True
        elif not quiet:
            # Без Telegram ID руководителя заводит python -m app.run: вход на
            # сайт — по логину и паролю, ID нужен только для мини-аппа.
            print(
                "[i] MANAGER_TG_USER_ID не задан. Руководителя с логином и паролем для сайта\n"
                "    создаст python -m app.run."
            )

        for code, title, desc, enabled in RULES:
            r = (await s.execute(select(Rule).where(Rule.code == code))).scalar_one_or_none()
            if r is None:
                s.add(Rule(code=code, title=title, description=desc, enabled=enabled))
        await s.flush()

        if demo:
            await _demo(s, manager, now)
        await s.commit()
    if not quiet:
        print("готово")


if __name__ == "__main__":
    asyncio.run(main(demo="--demo" in sys.argv))
