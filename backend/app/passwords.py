"""Пароли сотрудников для входа на сайт.

Хэш — scrypt из стандартной библиотеки: медленный намеренно, чтобы утёкшую
базу нельзя было перебрать за вечер. Сам пароль нигде не хранится и
показывается один раз — тому, кто его выдал.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_N, _R, _P = 2**14, 8, 1

# Без похожих символов (0/O, 1/l/I): пароль диктуют и переписывают руками.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

_CYR = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sh", "", "y", "", "e", "yu", "ya",
     "o", "q", "g", "h"],
))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(bytes.fromhex(dk)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), dk)


def generate_password() -> str:
    """Три группы по четыре знака: «k7mq-x2vd-p9wn» — ~60 бит, легко продиктовать."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3))


def login_from_name(name: str) -> str:
    """«Бекзод» → «bekzod», «Азиза Каримова» → «aziza.karimova»."""
    s = "".join(_CYR.get(ch, ch) for ch in name.lower())
    words = re.findall(r"[a-z0-9]+", s)
    return ".".join(words)[:64] or "user"


def valid_login(login: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", login))
