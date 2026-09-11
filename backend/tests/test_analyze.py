"""Проверка разбора сообщений без вызова модели.

Модель здесь не вызывается: проверяется всё вокруг неё — фильтр, вход, запись
результатов и защита от её ошибок. Именно там живут правила, которые нельзя
доверить модели: не выдумывать задачи, не терять сообщения, не ломаться на
повторном разборе.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dovod-analyze-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'a.db').as_posix()}"
os.environ["TELEGRAM_SESSION_PATH"] = str(_tmp / "s.session")
# Ключ из окружения машины не должен превращать офлайн-тест в платный вызов.
os.environ["ANTHROPIC_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.agents import analyze  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Chat,
    ChatKind,
    Message,
    Module,
    Proposal,
    ProposalKind,
    ProposalState,
    Staff,
    Task,
    TaskStatus,
    TaskType,
)

passed = failed = 0
NOW = datetime(2026, 9, 11, 9, 0)


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK  ] {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}" + (f" -- {detail}" if detail else ""))


def msg(text: str, **kw) -> Message:
    return Message(text=text, **kw)


def result(mid: int, kind: str, ref: str | None = None, **kw) -> dict:
    base = {
        "message_id": mid, "kind": kind, "task_ref": ref, "link_basis": kw.pop("basis", ""),
        "reading": kw.pop("reading", "понял так"), "action": kw.pop("action", "сделать так"),
        "confidence": kw.pop("confidence", 0.9),
        "draft": {"title": "", "dod": "", "type": "", "module": "", "due_at": None, "dod_add": None},
    }
    base["draft"].update(kw.pop("draft", {}))
    return base


async def main() -> None:
    # --- фильтр ------------------------------------------------------------
    r = analyze.prefilter_reason
    check("«ок» отсеивается", r(msg("ок")) is not None)
    check("«спасибо!» отсеивается", r(msg("спасибо!")) is not None)
    check("эмодзи отсеивается", r(msg("👍👍")) is not None)
    check("«не работает» проходит, хотя короткое", r(msg("не работает")) is None)
    check("«сделал» проходит — это продолжение", r(msg("сделал")) is None)
    check("узбекское «ishlamayapti» проходит", r(msg("ishlamayapti")) is None)
    check("короткий реплай проходит — реплай и есть продолжение", r(msg("вот так", reply_to_msg_id=5)) is None)
    check("короткая болтовня без рабочих слов отсеивается", r(msg("кто на обед")) is not None)
    check("картинка без подписи отсеивается с честной причиной",
          "скриншот" in (r(msg("", media_kind="MessageMediaPhoto")) or ""))

    # --- база ---------------------------------------------------------------
    await init_db()
    async with SessionLocal() as s:
        tim = Staff(tg_user_id=2, name="Тимур")
        chat = Chat(tg_chat_id=-100, title="SmartBooking · Разработка", kind=ChatKind.dev, enabled=True)
        s.add_all([tim, chat])
        await s.flush()
        task = Task(ref="SB-101", title="Синк тарифов падает по ночам",
                    definition_of_done="Три ночи логи чистые", type=TaskType.bug, module=Module.cm,
                    status=TaskStatus.in_progress, assignee_id=tim.id, last_moved_at=NOW)
        s.add(task)
        msgs = [
            Message(chat_id=chat.id, tg_msg_id=i, author_name="Тимур", text=t, sent_at=NOW - timedelta(minutes=10 - i))
            for i, t in enumerate([
                "победил, выкатил на прод",           # продолжение с верной ссылкой
                "у Registon не приходит бронь",       # новый запрос
                "кто-нибудь видел мою кружку",        # разговор (модель скажет noise)
                "это по SB-999, тоже упало",          # ссылка на несуществующую задачу
                "перенесём на понедельник",           # модель пропустит
                "ок",                                  # отсеет фильтр
            ], start=1)
        ]
        s.add_all(msgs)
        await s.commit()
        ids = [m.id for m in msgs]

    # --- вход модели --------------------------------------------------------
    async with SessionLocal() as s:
        t = (await s.execute(select(Task))).scalar_one()
        t.assignee = None
        batch = (await s.execute(select(Message).order_by(Message.id))).scalars().all()
        payload = json.loads(analyze.build_input("чат", list(batch[:2]), [t], {1: "SB-101"}, NOW))
        check("во входе есть открытые задачи", payload["open_tasks"][0]["ref"] == "SB-101")
        check("время идёт во входе, а не в системном промпте", payload["now_utc"] == "2026-09-11T09:00")
        check("текст сообщения передаётся", payload["messages"][1]["text"].startswith("у Registon"))

    # --- проход без ключа: фильтр работает, остальное ждёт ------------------
    async with SessionLocal() as s:
        stats = await analyze.analyze_pending(s, NOW)
        check("без ключа фильтр всё равно срабатывает", stats["prefiltered"] == 1, str(stats))
        waiting = (await s.execute(select(Message).where(Message.analyzed_at.is_(None)))).scalars().all()
        check("без ключа остальные сообщения ждут, а не теряются", len(waiting) == 5, f"{len(waiting)}")
        noise = (await s.execute(select(Proposal).where(Proposal.kind == ProposalKind.noise))).scalars().all()
        check("отсеянное фильтром получает исход", len(noise) == 1 and noise[0].model == "prefilter")

    # --- запись результатов модели -----------------------------------------
    fake = [
        result(ids[0], "progress", "SB-101", basis="mention"),
        result(ids[1], "new_task", draft={"title": "Бронь Ostrovok не доходит до Registon",
                                           "dod": "Тестовая бронь видна за 2 минуты",
                                           "type": "bug", "module": "CM"}),
        result(ids[2], "noise", action=""),
        result(ids[3], "duplicate", "SB-999", basis="mention", confidence=0.8),
        # ids[4] модель «пропустила»
    ]
    async with SessionLocal() as s:
        batch = (await s.execute(select(Message).where(Message.id.in_(ids[:5])))).scalars().all()
        tasks = {t.ref: t for t in (await s.execute(select(Task))).scalars()}
        st = await analyze.apply_results(s, list(batch), fake, tasks, "claude-opus-5", NOW)
        await s.commit()
        check("пропущенное моделью сообщение посчитано, а не выдумано", st["skipped"] == 1, str(st))

        props = {p.message_id: p for p in (await s.execute(select(Proposal))).scalars()}
        p0 = props[ids[0]]
        check("продолжение привязано к задаче", p0.kind == ProposalKind.progress and p0.task_id is not None)
        check("основание связи сохранено", p0.link_basis == "mention")
        p1 = props[ids[1]]
        check("новый запрос несёт черновик задачи",
              p1.kind == ProposalKind.new_task and p1.draft.get("module") == "CM")
        p2 = props[ids[2]]
        check("разговор не попадает во входящие", p2.state == ProposalState.rejected)
        p3 = props[ids[3]]
        check("ссылка на несуществующую задачу не принимается на веру",
              p3.kind == ProposalKind.new_task and p3.task_id is None, p3.reading[-60:])
        check("уверенность в неподтверждённой связи снижена", p3.confidence <= 0.4, f"{p3.confidence}")
        check("пропущенному сообщению исход не придуман", ids[4] not in props)

    # --- повторный разбор того же сообщения ---------------------------------
    async with SessionLocal() as s:
        m0 = (await s.execute(select(Message).where(Message.id == ids[0]))).scalar_one()
        m0.analyzed_at = None  # как при отмене задачи
        await s.commit()
    async with SessionLocal() as s:
        batch = (await s.execute(select(Message).where(Message.id == ids[0]))).scalars().all()
        tasks = {t.ref: t for t in (await s.execute(select(Task))).scalars()}
        try:
            await analyze.apply_results(s, list(batch), [result(ids[0], "question", "SB-101")], tasks, "m", NOW)
            await s.commit()
            n = len((await s.execute(select(Proposal).where(Proposal.message_id == ids[0]))).scalars().all())
            kind = (await s.execute(select(Proposal.kind).where(Proposal.message_id == ids[0]))).scalar_one()
            check("повторный разбор переписывает предложение, а не дублирует",
                  n == 1 and kind == ProposalKind.question, f"{n} шт., {kind}")
        except Exception as exc:  # noqa: BLE001
            check("повторный разбор переписывает предложение, а не дублирует", False, repr(exc)[:80])

    print()
    print("=" * 62)
    print(f"ИТОГ: {passed} пройдено, {failed} провалено")
    sys.exit(1 if failed else 0)


asyncio.run(main())
