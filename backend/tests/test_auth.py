"""Проверка валидации initData.

Тест на подпись — не формальность: если он зелёный при подделанных данных,
приложение открыто для любого желающего.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import verify_init_data  # noqa: E402

TOKEN = "123456:TEST-TOKEN-NOT-REAL"


def make_init_data(token: str = TOKEN, **overrides) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAtest",
        "user": json.dumps(
            {"id": 555, "first_name": "Бекзод", "last_name": "", "username": "bekzod"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    fields.update({k: str(v) for k, v in overrides.items()})
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def expect_error(fragment: str, init_data: str, token: str = TOKEN) -> None:
    try:
        verify_init_data(init_data, token)
    except ValueError as exc:
        assert fragment in str(exc), f"ожидали «{fragment}», получили «{exc}»"
        return
    raise AssertionError(f"подпись принята, хотя не должна была: {fragment}")


def run() -> None:
    # 1. Корректная подпись принимается и отдаёт пользователя.
    user = verify_init_data(make_init_data(), TOKEN)
    assert user.id == 555, user
    assert user.display_name == "Бекзод", user.display_name
    print("[OK  ] корректная подпись принимается")

    # 2. Подмена данных ломает подпись — иначе можно назваться кем угодно.
    tampered = make_init_data().replace("555", "999")
    expect_error("подпись не сходится", tampered)
    print("[OK  ] подменённый user_id отвергается")

    # 3. Подпись, сделанная чужим токеном, не подходит.
    expect_error("подпись не сходится", make_init_data(token="999:OTHER"))
    print("[OK  ] чужой токен отвергается")

    # 4. Просроченная подпись не вечна.
    old = make_init_data(auth_date=int(time.time()) - 40 * 60 * 60)
    expect_error("просрочена", old)
    print("[OK  ] просроченная подпись отвергается")

    # 5. Без hash — отказ, а не «ну ладно».
    without = urlencode({"auth_date": str(int(time.time())), "user": "{}"})
    expect_error("нет подписи", without)
    print("[OK  ] запрос без подписи отвергается")

    # 6. Без токена проверка невозможна — и это отказ, а не пропуск.
    expect_error("BOT_TOKEN не задан", make_init_data(), token="")
    print("[OK  ] без BOT_TOKEN вход закрыт")

    print("\nВсе проверки подписи пройдены.")


if __name__ == "__main__":
    run()
