"""Конфигурация «Довода». Все секреты — только из окружения.

Про два Telegram-подключения. Их именно два, и это не дублирование:

* **Telethon (user-сессия)** читает чаты. Он видит всё, что видит человек,
  включая группы без бота, — иначе «контролировать весь телеграм» нереализуемо.
  Он ничего не отправляет: `telethon_read_only` защищает от того, чтобы
  сообщения уходили от лица живого сотрудника.

* **Bot API (отдельный бот)** пишет: мини-апп, уведомления, эскалации,
  ответы в тредах. Исходящее от имени бота видно как машинное, и это честно.

Смешивать их — значит либо не видеть половину чатов, либо писать клиентам от
чужого имени.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # dovod/
REPO_ROOT = PROJECT_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", str(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- инфраструктура ---
    database_url: str = "sqlite+aiosqlite:///./dovod.db"
    api_host: str = "127.0.0.1"
    api_port: int = 8010
    # Мини-апп открывается внутри Telegram, поэтому источник — домен, на котором
    # он опубликован. Локально это vite dev-сервер.
    cors_origins: str = "http://localhost:5174"
    # Публичный HTTPS-адрес мини-аппа. Telegram не откроет http и localhost.
    miniapp_url: str = ""
    manager_name: str = "Руководитель"
    # Имя бота для ссылки входа на сайт. Пусто — спросим у Telegram (getMe).
    bot_username: str = ""

    # --- Telegram: чтение (MTProto user-API) ---
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_path: str = "./data/dovod.session"
    # Предохранитель. Telethon-клиент поднимается только на чтение; любая
    # попытка отправки при включённом флаге падает, а не уходит в чат.
    telethon_read_only: bool = True

    # --- Telegram: запись (Bot API) ---
    bot_token: str = ""
    # Куда падает утренняя сводка и эскалации, если получатель не определён.
    manager_tg_user_id: int = 0

    # --- Anthropic ---
    anthropic_api_key: str = ""
    # Средняя модель по умолчанию — Sonnet (между Haiku и Opus). Глубину даёт
    # не столько модель, сколько effort и база знаний; Sonnet на разборе и
    # диагнозе дешевле Opus при сопоставимом качестве на этих задачах.
    model_analyze: str = "claude-sonnet-5-5"
    model_digest: str = "claude-sonnet-5-5"
    # Разбор — массовая классификация, глубина не нужна и стоит денег.
    effort_analyze: str = "low"
    effort_digest: str = "medium"

    # --- пайплайн ---
    # Двухэтапный разбор. Модель на каждом сообщении из четырёх чатов — это
    # тысячи вызовов в неделю; дешёвый фильтр отсеивает явные разговоры, и
    # только остаток уходит в модель вместе со списком открытых задач чата.
    prefilter_enabled: bool = True
    prefilter_min_chars: int = 12
    analyze_batch_size: int = 30
    analyze_interval_minutes: int = 15
    backfill_limit: int = 200
    # Порог, ниже которого предложение не показывается совсем. На пилоте выше
    # обычного: доверие к боту дороже полноты, один ложный «перенос срока»
    # отобьёт желание нажимать «Применить».
    proposal_min_confidence: float = 0.6

    # --- обещания клиенту ---
    sla_first_reply_minutes: int = 15
    sla_resolve_minutes: int = 240

    # --- авто-создание обращений из чата ---
    # Сообщение из support-чата, которое разбор счёл новым запросом, становится
    # обращением (Ticket) само — иначе SLA и диагноз ждут, пока человек заведёт
    # заявку руками, и смысл автоматики теряется. Выключатель — на случай, если
    # support-чат ещё не размечен и ловится лишнее.
    auto_ticket_enabled: bool = True
    auto_ticket_max_per_cycle: int = 20
    # Сообщения старше этого (догруженная история) разбираются, но обращений
    # с таймером SLA не создают: по ним давно ответили, а «просрочка» в сотни
    # часов только засыпала бы руководителя эскалациями.
    fresh_hours: int = 24
    # Служебный аккаунт поддержки: отели пишут ему в личку, и такая переписка —
    # те же обращения, что в чате поддержки. Включено — из включённых личных
    # переписок тоже создаются заявки с таймером SLA.
    private_as_support: bool = False
    # Основание читать новые чаты (например «служебный аккаунт, рабочая
    # переписка»). Задано — чаты, появившиеся после запуска, включаются сами,
    # с текущего момента и с этой записью о согласии. Пусто — новые чаты
    # регистрируются выключенными и ждут руководителя.
    new_chats_consent: str = ""

    # --- авто-диагноз ---
    # Новые обращения типа «баг/проблема» без диагноза воркер разбирает сам.
    # Выключается на случай, если ключ Anthropic дорог или модель недоступна.
    auto_diagnose_enabled: bool = True
    # Потолок диагнозов за один проход: диагноз — вызов модели, и необъятная
    # пачка на первом запуске (когда непродиагностировано всё разом) и упёрлась
    # бы в лимит, и выжгла бы бюджет. Остаток уедет на следующем проходе.
    auto_diagnose_max_per_cycle: int = 5

    # --- самопополнение базы знаний из закрытых задач ---
    # Закрытая задача-баг/проблема разбирается агентом на многоразовый урок и
    # ложится в базу знаний. Потолок за проход небольшой: это вызов модели, а
    # закрытые задачи не горят — разберутся за несколько проходов.
    learn_from_tasks_enabled: bool = True
    learn_max_per_cycle: int = 3

    # --- сводка и эскалация ---
    digest_hour_local: int = 9
    timezone: str = "Asia/Tashkent"
    # Недельный отчёт по сотрудникам. Понедельник утром намеренно: неделя
    # закрыта, а разговор по её итогам есть с чего начать в тот же день.
    # 0 — понедельник … 6 — воскресенье.
    weekly_report_weekday: int = 0
    weekly_report_hour_local: int = 9
    escalate_first_reply_minutes: int = 25
    escalate_idle_days: int = 5
    # Стоимость часа обслуживания. На ней держится вся экономика в карточке
    # клиента: поменяете ставку — поменяется вывод, кого удерживать, а кого
    # чинить. Цифра обязана быть осознанной, поэтому она в конфиге, а не в коде.
    service_hour_cost: int = 60000  # сум

    @field_validator("telegram_api_id", "manager_tg_user_id", mode="before")
    @classmethod
    def _empty_int_is_zero(cls, value: object) -> object:
        """Пустая строка в .env — «не задано», а не ошибка конфигурации.

        Иначе `TELEGRAM_API_ID=` из .env.example роняет сервис на импорте, и
        мини-апп не поднимется, хотя к Telethon он отношения не имеет.
        """
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    Path(s.telegram_session_path).parent.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
