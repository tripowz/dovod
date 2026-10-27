"""Сквозная проверка мини-аппа в браузере на демо-данных.

Поднимает API на временной базе с демо-данными, открывает мини-апп в
headless Chromium с подписанными данными Telegram — ровно такими, какие
передал бы клиент, — и проходит вкладки глазами руководителя и сотрудника.
Настоящий SDK Telegram подменяется заглушкой: вне Telegram он отдаёт пустую
подпись, и приложение честно отказалось бы работать.

    python tests/e2e_miniapp.py [папка-для-скриншотов]

Нужна собранная сборка мини-аппа: cd miniapp && npm run build
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

BACKEND = Path(__file__).resolve().parents[1]
TOKEN = "123456:TEST-TOKEN-NOT-REAL"
MANAGER_ID = 555
WEB_PASSWORD = "k7mq-x2vd-p9wn"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="dovod-shots-"))
OUT.mkdir(parents=True, exist_ok=True)

_tmp = Path(tempfile.mkdtemp(prefix="dovod-e2e-"))
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{(_tmp / 'e2e.db').as_posix()}",
    "BOT_TOKEN": TOKEN,
    "MANAGER_TG_USER_ID": str(MANAGER_ID),
    "MANAGER_NAME": "Бекзод",
    "TELEGRAM_SESSION_PATH": str(_tmp / "s.session"),
    "ANTHROPIC_API_KEY": "",
    "MINIAPP_URL": "",
    })
sys.path.insert(0, str(BACKEND))

from playwright.sync_api import Page, sync_playwright  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


def init_data(uid: int, name: str) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": uid, "first_name": name}, ensure_ascii=False, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


STUB = """
window.Telegram = { WebApp: {
  initData: __INIT__, colorScheme: "light",
  ready() {}, expand() {},
  MainButton: { setText() {}, show() {}, hide() {}, enable() {}, disable() {}, onClick() {}, offClick() {} },
  BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
  HapticFeedback: { impactOccurred() {}, notificationOccurred() {} },
  showAlert(m, cb) { (window.__alerts = window.__alerts || []).push(m); cb && cb(); },
  showConfirm(m, cb) { cb(true); },
  openTelegramLink() {}
}};
"""


async def prepare() -> None:
    """Демо-данные сида плюс два предложения во входящих."""
    from sqlalchemy import select

    from app import seed
    from app.db import SessionLocal
    from app.models import Chat, ChatKind, Hotel, Message, Proposal, ProposalKind, ProposalState, Staff, Task

    await seed.main(demo=True)
    now = datetime.utcnow()
    from app import passwords
    async with SessionLocal() as s:
        boss = (await s.execute(select(Staff).where(Staff.tg_user_id == MANAGER_ID))).scalar_one()
        boss.login, boss.password_hash = "bekzod", passwords.hash_password(WEB_PASSWORD)
        await s.commit()
    async with SessionLocal() as s:
        chat = Chat(tg_chat_id=-1001, title="SmartBooking · Продукт", kind=ChatKind.team, enabled=True)
        s.add(chat)
        await s.flush()
        sync_task = (await s.execute(select(Task).where(Task.ref == "SB-101"))).scalar_one()
        m1 = Message(chat_id=chat.id, tg_msg_id=1, author_name="Дилшод", sent_at=now - timedelta(minutes=40),
                     analyzed_at=now, text="у Registon опять не подтягивается бронь с Ostrovok, они утром звонили")
        m2 = Message(chat_id=chat.id, tg_msg_id=2, author_name="Тимур", sent_at=now - timedelta(minutes=20),
                     analyzed_at=now, text="победил — был таймаут в очереди выгрузки, выкатил на прод")
        registon = (await s.execute(select(Hotel).where(Hotel.name == "Hotel Registon"))).scalar_one()
        hchat = Chat(tg_chat_id=-1002, title="Hotel Registon & Smartbooking", kind=ChatKind.support,
                     enabled=True, hotel_id=registon.id)
        s.add(hchat)
        await s.flush()
        for i in range(4):
            hm = Message(chat_id=hchat.id, tg_msg_id=10 + i, author_name="Малика",
                         sent_at=now - timedelta(days=2 + i * 5), analyzed_at=now,
                         text=f"опять не пришла бронь с Ostrovok ({i + 1})")
            s.add(hm)
            await s.flush()
            s.add(Proposal(message_id=hm.id, kind=ProposalKind.new_task, state=ProposalState.history,
                           confidence=0.7, model="demo", draft={"type": "bug", "module": "CM"}))
        m3 = Message(chat_id=chat.id, tg_msg_id=3, author_name="Малика", sent_at=now - timedelta(days=12),
                     analyzed_at=now, text="можно ли выгружать отчёт по гостям в Excel?")
        s.add_all([m1, m2, m3])
        await s.flush()
        s.add_all([
            Proposal(message_id=m1.id, kind=ProposalKind.new_task, confidence=0.91, model="demo",
                     reading="Есть проблема и пострадавший клиент, но никто не назван ответственным.",
                     action="Завести задачу на синхронизацию с Ostrovok для Registon.",
                     draft={"title": "Бронь с Ostrovok не доходит до Hotel Registon",
                            "dod": "Тестовая бронь с Ostrovok появляется в Календаре Registon за 2 минуты.",
                            "type": "bug", "module": "CM"}),
            Proposal(message_id=m2.id, kind=ProposalKind.progress, task_id=sync_task.id, link_basis="mention",
                     confidence=0.9, model="demo",
                     reading="Исполнитель сообщает, что работа сделана, а задача всё ещё в работе.",
                     action="Перевести SB-101 на проверку и позвать автора принимать."),
            Proposal(message_id=m3.id, kind=ProposalKind.new_task, state=ProposalState.history,
                     confidence=0.8, model="demo", reading="Просят выгрузку отчёта по гостям.",
                     action="Завести запрос на выгрузку в Excel.",
                     draft={"title": "Выгрузка отчёта по гостям в Excel", "type": "request", "module": "PMS"}),
        ])
        await s.commit()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_api(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND, env=os.environ.copy(),
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("API не поднялся за 30 секунд")


def open_as(browser, base: str, uid: int, name: str) -> tuple:
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="ru-RU")
    ctx.route("**/telegram-web-app.js", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body="/* заглушка SDK */"))
    ctx.add_init_script(STUB.replace("__INIT__", json.dumps(init_data(uid, name))))
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    page.goto(base)
    page.wait_for_load_state("networkidle")
    return ctx, page, errors


def tab(page: Page, label: str) -> None:
    page.locator("nav").get_by_role("button", name=label, exact=True).click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(250)


def toast(page: Page, click) -> str:
    """Нажимает и ждёт новый тост.

    Тост живёт 3,6 секунды: если просто прочитать его после нажатия, можно
    получить сообщение от предыдущего действия. Первый прогон так и врал —
    каждая проверка видела тост соседней.
    """
    old = page.locator("[role=status]").all_inner_texts()
    click()
    try:
        page.wait_for_function(
            "prev => { const el = document.querySelector('[role=status]');"
            " return el !== null && el.innerText !== prev; }",
            arg=old[0] if old else "",
            timeout=6000,
        )
    except Exception:  # noqa: BLE001
        return ""
    return page.locator("[role=status]").first.inner_text()


def visible(page: Page, text: str | re.Pattern, timeout: int = 5000) -> bool:
    """Ждёт появления текста, а не проверяет мгновенно: экраны грузят данные
    после отрисовки, и мгновенная проверка ловит состояние «Загрузка…»."""
    try:
        page.get_by_text(text).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"))


def as_manager(browser, base: str) -> list[str]:
    ctx, page, errors = open_as(browser, base, MANAGER_ID, "Бекзод")

    check("руководителю приложение открывается на сводке", visible(page, "Утро, Бекзод"))
    check("в сводке видно, что горит", visible(page, "Горит прямо сейчас"))
    shot(page, "01_manager_digest")

    tab(page, "Входящие")
    check("во входящих виден запрос из чата", visible(page, "у Registon опять"))
    check("запрос из истории не во входящих", not page.get_by_text("отчёт по гостям").is_visible())
    shot(page, "02_manager_inbox")
    page.get_by_role("button", name="История", exact=True).click()
    check("запрос из истории — в архиве", visible(page, "отчёт по гостям"))
    check("в архиве нет «Не то»", page.get_by_role("button", name="Не то").count() == 0)
    check("вкладка «История» выбрана",
          page.get_by_role("button", name="История", exact=True).get_attribute("aria-pressed") == "true")
    page.wait_for_timeout(300)  # переключатель меняет фон плавно
    shot(page, "02b_manager_history")
    page.get_by_role("button", name=re.compile("^Новые")).click()

    page.get_by_role("button", name="В задачи", exact=True).first.click()
    blocked = page.get_by_role("button", name="Заполните обязательные поля")
    check("форма не пускает без ответственного", blocked.is_visible() and blocked.is_disabled())
    page.get_by_role("button", name="Тимур", exact=True).click()
    shot(page, "03_manager_new_task_form")
    t = toast(page, lambda: page.get_by_role("button", name="Создать задачу").click())
    check("задача из запроса создана", "создана" in t, t)

    page.get_by_role("button", name=re.compile("Продолжения")).click()
    check("продолжение из чата видно", visible(page, "победил"))
    t = toast(page, lambda: page.get_by_role("button", name="Применить", exact=True).first.click())
    check("продолжение применено к задаче", "SB-101" in t and "проверку" in t, t)

    tab(page, "Задачи")
    page.get_by_role("button", name=re.compile("Синхронизация тарифов")).first.click()
    check("в карточке есть критерий готовности", visible(page, "Критерий готовности"))
    review = page.get_by_role("button", name="На проверке", exact=True)
    check("задача стоит на проверке после применения", review.get_attribute("aria-pressed") == "true")
    check("метка сообщения в нити по-русски", visible(page, re.compile(r"^решение$")))
    shot(page, "04_manager_task_detail")
    page.locator("main").get_by_role("button", name="Задачи", exact=True).click()

    tab(page, "Заявки")
    check("разбор ИИ готов к открытию чата", visible(page, re.compile(r"^Разбор ИИ$")))
    check("черновик ответа на месте", visible(page, "Черновик ответа"))
    shot(page, "05_manager_ticket_ai")
    t = toast(page, lambda: page.get_by_role("button", name="Ответил клиенту").first.click())
    check("ответ клиенту фиксируется", "таймер" in t, t)
    check("разбор схлопнулся после ответа", visible(page, re.compile("Разбор ИИ отработан")))

    tab(page, "Клиенты")
    page.get_by_role("button", name=re.compile("Hotel Registon")).first.click()
    check("экономика клиента с выводом", visible(page, re.compile("починить причину")))
    check("в выводе по клиенту целы запятые", visible(page, re.compile("в том, чтобы")))
    check("карточка считает переписку", visible(page, "Переписка за 30 дней"))
    check("сигнал из переписки", visible(page, re.compile("Чаще всего про CM: 4 из 4")))
    check("связанный чат в карточке", visible(page, "Hotel Registon & Smartbooking"))
    shot(page, "06_manager_client")
    t = toast(page, lambda: page.get_by_role("button", name="В задачи", exact=True).first.click())
    check("предложение по удержанию стало задачей", "создана" in t, t)

    tab(page, "Отчёт")
    visible(page, "Утро, Бекзод")
    shot(page, "07_manager_digest_after")
    ctx.close()
    return errors


def as_employee(browser, base: str) -> list[str]:
    # Демо-сотрудник Тимур заведён с ID -1: такого аккаунта в Telegram нет,
    # поэтому подпись для него можно сделать только в тесте.
    ctx, page, errors = open_as(browser, base, -1, "Тимур")
    check("сотрудник начинает со входящих", visible(page, "Что бот услышал в чатах"))
    nav = page.locator("nav").inner_text()
    check("сотруднику вкладка «Отчёт» не показывается", "Отчёт" not in nav, nav.replace("\n", " · "))

    # Проверка доступа идёт мимо браузера: намеренный 403 из страницы браузер
    # записал бы в консоль как ошибку, и проверка консоли стала бы бессмысленной.
    req = urllib.request.Request(base + "api/digest", headers={"X-Telegram-Init-Data": init_data(-1, "Тимур")})
    try:
        status = urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as exc:
        status = exc.code
    check("сводка по команде закрыта и через API", status == 403, str(status))

    tab(page, "Задачи")
    page.get_by_role("button", name=re.compile("Бронь с Ostrovok не попадает")).first.click()
    visible(page, "Критерий готовности")
    done = page.get_by_role("button", name="Готово", exact=True)
    check("чужую задачу нельзя принять — кнопка погашена", done.is_disabled())
    check("причина объяснена без рода", visible(page, re.compile("принять её может только автор")))
    shot(page, "08_employee_task")
    ctx.close()
    return errors


def as_web(browser, base: str) -> list[str]:
    """Сайт в обычном браузере, без Telegram и без бота: логин и пароль."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="ru-RU")
    # Настоящий SDK вне Telegram не нужен — и в тестовой сети недоступен.
    ctx.route("**/telegram-web-app.js", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body="/* вне Telegram */"))
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
    page.goto(base)
    check("в браузере — вход по логину и паролю", visible(page, "Логин и пароль выдаёт руководитель"))
    shot(page, "09_web_login")

    page.get_by_label("Логин").fill("bekzod")
    page.get_by_label("Пароль").fill("неверный")
    page.get_by_role("button", name="Войти", exact=True).click()
    check("неверный пароль — понятная ошибка", visible(page, "Неверный логин или пароль"))
    page.get_by_label("Пароль").fill(WEB_PASSWORD)
    page.get_by_role("button", name="Войти", exact=True).click()
    check("верный пароль — вход", visible(page, "Сводка", timeout=8000))
    check("в браузере есть «Выйти»", page.get_by_role("button", name="Выйти").is_visible())
    page.wait_for_load_state("networkidle")
    shot(page, "10_web_report")

    tab(page, "Команда")
    page.get_by_role("button", name="Чаты", exact=True).click()
    check("экран «Чаты» — что читается", visible(page, re.compile(r"Читается \d+ из \d+")))
    page.get_by_role("button", name="Все", exact=True).click()
    page.get_by_label("Поиск чатов").fill("Registon")
    check("поиск по чатам", visible(page, "Hotel Registon & Smartbooking"))
    page.wait_for_timeout(300)
    shot(page, "11_web_chats")

    page.get_by_role("button", name="Сотрудники", exact=True).click()
    page.get_by_label("Имя").fill("Малика Каримова")
    page.get_by_role("button", name="Добавить и выдать пароль").click()
    check("сотрудник добавлен с логином и паролем", visible(page, "malika.karimova"))
    shot(page, "12_web_new_staff")

    page.reload()
    page.wait_for_load_state("networkidle")
    check("после перезагрузки вход сохранён", page.get_by_role("button", name="Выйти").is_visible())
    page.get_by_role("button", name="Выйти").click()
    check("выход возвращает к входу", visible(page, "Логин и пароль выдаёт руководитель"))
    ctx.close()
    return errors


def main() -> None:
    asyncio.run(prepare())
    port = free_port()
    api = start_api(port)
    base = f"http://127.0.0.1:{port}/"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            print("=" * 62 + "\nРУКОВОДИТЕЛЬ\n" + "=" * 62)
            errors = as_manager(browser, base)
            print("=" * 62 + "\nСОТРУДНИК\n" + "=" * 62)
            errors += as_employee(browser, base)
            print("=" * 62 + "\nСАЙТ В БРАУЗЕРЕ\n" + "=" * 62)
            errors += as_web(browser, base)
            browser.close()
    finally:
        api.terminate()
        api.wait(timeout=10)

    real = [e for e in errors if "favicon" not in e]
    check("ни одной ошибки в консоли браузера", not real, "; ".join(real)[:200])
    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    print(f"Скриншоты: {OUT}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
