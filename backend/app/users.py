"""Логины и пароли сотрудников для сайта.

    python -m app.users                    — кто может войти
    python -m app.users password bekzod    — новый пароль (покажется один раз)
    python -m app.users login 3 aziza      — сменить логин сотруднику №3

То же самое руководитель делает на сайте, в «Команде». Команда нужна, когда
войти на сайт некому — например, забыли пароль руководителя.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from . import passwords
from .db import SessionLocal, init_db
from .models import Staff


async def main(argv: list[str]) -> int:
    await init_db()
    async with SessionLocal() as s:
        if not argv:
            rows = (await s.execute(select(Staff).order_by(Staff.active.desc(), Staff.name))).scalars().all()
            print("  №   логин             пароль  роль          имя")
            for r in rows:
                role = "руководитель" if r.is_manager else "сотрудник"
                state = "" if r.active else "  (отключён)"
                print(f"{r.id:>4}   {(r.login or '—'):<16}  {'есть' if r.password_hash else 'нет ':<6}  {role:<12}  {r.name}{state}")
            print("\nНовый пароль: python -m app.users password <логин или №>")
            return 0

        if argv[0] == "password" and len(argv) == 2:
            who = argv[1].lower()
            st = await s.get(Staff, int(who)) if who.isdigit() else (
                await s.execute(select(Staff).where(Staff.login == who))
            ).scalar_one_or_none()
            if st is None:
                print(f"Сотрудника «{argv[1]}» нет. Список: python -m app.users")
                return 1
            if not st.login:
                st.login = passwords.login_from_name(st.name)
            pw = passwords.generate_password()
            st.password_hash = passwords.hash_password(pw)
            await s.commit()
            print(f"{st.name}\n  логин:  {st.login}\n  пароль: {pw}\nПароль показан один раз; старый больше не работает.")
            return 0

        if argv[0] == "login" and len(argv) == 3 and argv[1].isdigit():
            st = await s.get(Staff, int(argv[1]))
            new = argv[2].lower()
            if st is None:
                print(f"Сотрудника №{argv[1]} нет.")
                return 1
            if not passwords.valid_login(new):
                print("Логин — латиница, цифры, точка, дефис; от 2 символов.")
                return 2
            taken = (await s.execute(select(Staff).where(Staff.login == new))).scalar_one_or_none()
            if taken is not None and taken.id != st.id:
                print(f"Логин «{new}» уже у «{taken.name}».")
                return 1
            st.login = new
            await s.commit()
            print(f"{st.name}: логин {new}")
            return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
