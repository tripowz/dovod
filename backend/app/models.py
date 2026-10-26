"""Модель данных «Довода».

Три инварианта, на которых держится всё остальное.

1. **Провенанс.** Ни одна задача не существует без цепочки
   `Task -> ThreadLink -> Message -> Chat`. Если утверждение нельзя проследить
   до конкретного сообщения в Telegram, его нет. Заимствовано у pm-agent
   осознанно: без этого трекер превращается в свалку пересказов.

2. **Предложение, а не действие.** Всё, что придумала модель, приезжает в
   `Proposal` и ждёт человека. Единственное исключение — эскалация
   (`Rule`): она срабатывает сама, потому что нарушенное обещание,
   о котором никто не узнал, — это и есть та дыра, ради которой всё затевалось.

3. **Ничего не теряется.** Задачу можно признать ошибочной или дублем, но
   исходные сообщения при этом возвращаются в очередь разбора, а не исчезают.
   Отмена, стоящая потери данных, не используется — и тогда мусорные задачи
   копятся вечно.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# --------------------------------------------------------------------------
# Перечисления
# --------------------------------------------------------------------------


class ChatKind(str, enum.Enum):
    support = "support"  # чат техподдержки — сюда пишут отельеры
    team = "team"  # рабочая группа команды
    dev = "dev"  # разработка
    sales = "sales"
    private = "private"  # личная переписка: подключается только с согласия
    other = "other"


class TaskType(str, enum.Enum):
    """Что это по сути.

    `bug` и `problem` разделены намеренно: рост багов означает, что ломается
    код, рост проблем — что продукт неудобен. Это разные выводы и разные
    решения, а в одной куче они неразличимы.
    """

    bug = "bug"  # сломано то, что раньше работало
    problem = "problem"  # работает, но мешает: медленно, неудобно, запутанно
    request = "request"  # просят новое
    question = "question"  # нужен ответ, а не работа


class Module(str, enum.Enum):
    """Часть системы с точки зрения отельера, а не название репозитория."""

    pms = "PMS"  # номерной фонд, брони, календарь
    cm = "CM"  # синхронизация с Booking, Ostrovok, Expedia
    website = "WEBSITE"  # сайты отелей
    booking = "BOOKING"  # модуль бронирования на сайте
    mobile = "MOBILE"  # приложение для отельеров
    billing = "BILLING"  # счета, тарифы, задолженности
    api = "API"  # интеграции и вебхуки
    admin = "ADMIN"  # админка и CRM
    unknown = "UNKNOWN"


class TaskStatus(str, enum.Enum):
    new = "new"
    in_progress = "in_progress"
    review = "review"  # исполнитель сдал, ждёт приёмки автором
    done = "done"
    merged = "merged"  # закрыта как дубль другой задачи
    cancelled = "cancelled"  # признана ошибочно созданной


class TicketStatus(str, enum.Enum):
    new = "new"
    working = "working"
    waiting_client = "waiting_client"
    waiting_dev = "waiting_dev"  # передано в разработку, но клиенту ещё не ответили
    resolved = "resolved"
    closed = "closed"


class ProposalKind(str, enum.Enum):
    """Что именно предлагает модель по разобранному сообщению."""

    new_task = "new_task"  # новый запрос
    progress = "progress"  # исполнитель сообщил, что работа сделана
    deadline = "deadline"  # перенос срока
    scope = "scope"  # к задаче добавили требование
    duplicate = "duplicate"  # то же самое из другого чата
    question = "question"  # вопрос по задаче, ничего не меняет
    noise = "noise"  # разговор, в трекер не идёт


class ProposalState(str, enum.Enum):
    pending = "pending"
    applied = "applied"
    rejected = "rejected"
    orphaned = "orphaned"  # задача, к которой оно относилось, исчезла


class NoteRole(str, enum.Enum):
    assignee = "assignee"
    manager = "manager"


class RiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


def _enum(py_enum: type[enum.Enum], length: int = 20) -> SAEnum:
    """VARCHAR-колонка, читаемая обратно как enum, а не как строка.

    Без `values_callable` SQLAlchemy хранил бы имена участников, а не значения;
    для `Module` это принципиально — имя `pms`, значение `PMS`.
    """
    return SAEnum(
        py_enum,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


# --------------------------------------------------------------------------
# Источники и сырьё
# --------------------------------------------------------------------------


class Chat(Base):
    """Подключённый чат.

    `enabled` выключен по умолчанию для личных переписок: групповой чат люди
    воспринимают как рабочее пространство, личный — нет. Включение такого
    источника требует отдельного шага и записывается в `consent_note`.
    """

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[ChatKind] = mapped_column(_enum(ChatKind), default=ChatKind.other)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    consent_note: Mapped[str] = mapped_column(Text, default="")
    last_seen_msg_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    messages: Mapped[list[Message]] = relationship(back_populates="chat")


class Message(Base):
    """Сырьё. Пишется один раз, не редактируется, не удаляется."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "tg_msg_id", name="uq_msg_chat_tg"),
        Index("ix_msg_unanalyzed", "analyzed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), index=True)
    tg_msg_id: Mapped[int] = mapped_column(Integer)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    # Реплай — самый надёжный признак продолжения: он не требует догадок.
    reply_to_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    chat: Mapped[Chat] = relationship(back_populates="messages")
    proposal: Mapped[Proposal | None] = relationship(
        back_populates="message", uselist=False
    )


# --------------------------------------------------------------------------
# Клиенты
# --------------------------------------------------------------------------


class Hotel(Base):
    """Отель-клиент.

    `mrr` и `service_hours` вместе отвечают на вопрос, который иначе решается
    на ощущение: сколько клиент приносит против того, во что обходится.
    Без этой пары «удержать любой ценой» звучит одинаково разумно для отеля,
    который платит втрое больше стоимости обслуживания, и для того, который
    съедает тариф целиком.
    """

    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    city: Mapped[str] = mapped_column(String(120), default="")
    rooms: Mapped[int] = mapped_column(Integer, default=0)
    tariff: Mapped[str] = mapped_column(String(160), default="")
    mrr: Mapped[int] = mapped_column(Integer, default=0)  # сум в месяц
    service_hours: Mapped[float] = mapped_column(Float, default=0.0)  # часов за месяц
    payment_note: Mapped[str] = mapped_column(String(255), default="")
    risk: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel), default=RiskLevel.low)
    # Признаки оттока, посчитанные кодом, и предложения, написанные моделью.
    # Хранятся раздельно: первое — факты, второе — мнение, и смешивать их
    # в одном поле значит потерять возможность доверять первому.
    signals: Mapped[list | None] = mapped_column(JSON, default=list)
    offers: Mapped[list | None] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tickets: Mapped[list[Ticket]] = relationship(back_populates="hotel")
    tasks: Mapped[list[Task]] = relationship(back_populates="hotel")


class HotelContact(Base):
    """Кто из отеля пишет в поддержку.

    Нужна, чтобы разбор обращения начинался не с «незнакомец прислал текст»,
    а с «администратор Registon, тариф такой-то, третье обращение за неделю».
    """

    __tablename__ = "hotel_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), index=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")


# --------------------------------------------------------------------------
# Сотрудники
# --------------------------------------------------------------------------


class Staff(Base):
    """Сотрудник. Привязка к Telegram — единственный способ входа в мини-апп."""

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    is_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    # Потолок одновременных задач. Не запрет, а порог для сводки: выше него
    # человек перестаёт заканчивать и начинает переключаться.
    wip_limit: Mapped[int] = mapped_column(Integer, default=6)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --------------------------------------------------------------------------
# Задачи
# --------------------------------------------------------------------------


class Task(Base):
    """Задача.

    `definition_of_done` объявлен NOT NULL без значения по умолчанию намеренно:
    задача без проверяемого критерия готовности — главная причина, по которой
    работа не доводится. Пустая строка сюда попасть может только через прямой
    SQL; API её не принимает.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_task_open", "status", "last_moved_at"),
        Index("ix_task_hotel_open", "hotel_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Человекочитаемый номер (SB-143). Отдельная последовательность от всего
    # остального: пропуск в нумерации задач читается как потерянная задача.
    ref: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(300))
    definition_of_done: Mapped[str] = mapped_column(Text)

    type: Mapped[TaskType] = mapped_column(_enum(TaskType), index=True)
    module: Mapped[Module] = mapped_column(_enum(Module), default=Module.unknown, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 низкий … 3 высокий
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus), default=TaskStatus.new, index=True
    )

    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Автор задачи — единственный, кто может поставить `done`. Это и есть
    # механизм против «сделал и забыл»: приёмка отделена от исполнения.
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), nullable=True, index=True
    )
    hotel_id: Mapped[int | None] = mapped_column(
        ForeignKey("hotels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Задача, в которую эта была объединена как дубль.
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )

    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    estimate_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    spent_hours: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Момент последнего содержательного движения. Из него считается
    # «температура» — сколько дней задача стоит. Приоритет все ставят высокий,
    # а дни простоя подделать нельзя.
    last_moved_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    assignee: Mapped[Staff | None] = relationship(foreign_keys=[assignee_id])
    author: Mapped[Staff | None] = relationship(foreign_keys=[author_id])
    hotel: Mapped[Hotel | None] = relationship(back_populates="tasks")
    notes: Mapped[list[TaskNote]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    thread: Mapped[list[ThreadLink]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        return self.status not in (
            TaskStatus.done,
            TaskStatus.merged,
            TaskStatus.cancelled,
        )

    def idle_days(self, now: datetime) -> int:
        return max((now - self.last_moved_at).days, 0)

    def is_overdue(self, now: datetime) -> bool:
        return bool(self.due_at and self.is_open and self.due_at < now)


class TaskNote(Base):
    """Заметка по задаче.

    Роль хранится отдельно от автора: заметка руководителя — это указание,
    заметка исполнителя — отчёт. В ленте они читаются по-разному, и склеивать
    их в «просто комментарий» значит потерять эту разницу.
    """

    __tablename__ = "task_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[NoteRole] = mapped_column(_enum(NoteRole))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="notes")


class TaskEvent(Base):
    """История задачи.

    `pending` отмечает запись-ожидание («ждёт приёмки»): в ленте такие всегда
    держатся в конце, иначе новое событие встаёт над незавершённым шагом и
    история читается задом наперёд.
    """

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    text: Mapped[str] = mapped_column(String(400))
    actor_name: Mapped[str] = mapped_column(String(160), default="")
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="events")


class ThreadLink(Base):
    """Сообщение из чата, привязанное к задаче.

    Это и есть «нить»: запрос живёт дольше одного сообщения, его уточняют
    через день и переносят через неделю. Без нити карточка устаревает молча.
    """

    __tablename__ = "thread_links"
    __table_args__ = (UniqueConstraint("task_id", "message_id", name="uq_thread_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    # request | clarification | question | resolution | postpone | complaint
    tag: Mapped[str] = mapped_column(String(24), default="request")

    task: Mapped[Task] = relationship(back_populates="thread")
    message: Mapped[Message] = relationship()


# --------------------------------------------------------------------------
# Заявки клиентов
# --------------------------------------------------------------------------


class Ticket(Base):
    """Обращение отеля в поддержку.

    Отдельная сущность, а не тип задачи: у обращения есть ожидающий человек и
    обещанное время ответа, и смешивать это с внутренней работой — верный
    способ пропустить и то, и другое.

    `first_reply_at` заполняется только настоящим ответом клиенту. Передача
    вопроса в разработку его не заполняет: для отеля проблема жива, пока ему
    не написали, и именно этот разрыв даёт «закрыто без ответа клиенту».
    """

    __tablename__ = "tickets"
    __table_args__ = (Index("ix_ticket_open", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    hotel_id: Mapped[int | None] = mapped_column(
        ForeignKey("hotels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )

    author_name: Mapped[str] = mapped_column(String(160), default="")
    author_role: Mapped[str] = mapped_column(String(80), default="")
    text: Mapped[str] = mapped_column(Text)

    type: Mapped[TaskType] = mapped_column(_enum(TaskType), default=TaskType.question)
    module: Mapped[Module] = mapped_column(_enum(Module), default=Module.unknown)
    status: Mapped[TicketStatus] = mapped_column(
        _enum(TicketStatus), default=TicketStatus.new, index=True
    )

    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    first_reply_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Оценка клиента после закрытия. Без неё «контроль качества» держится на
    # нашем собственном мнении о своей работе.
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)

    hotel: Mapped[Hotel | None] = relationship(back_populates="tickets")
    analysis: Mapped[Analysis | None] = relationship(
        back_populates="ticket", uselist=False
    )


class Analysis(Base):
    """Разбор обращения моделью: то, что готово до того, как открыли чат.

    Черновик ответа хранится, но не отправляется. Автоответ на письмо
    «опять не работает, мы так работать не можем» читается как отписка и злит
    сильнее, чем пятнадцать минут молчания, — а именно такие письма приходят
    от клиентов, которых ещё можно удержать.
    """

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, index=True
    )

    who: Mapped[str] = mapped_column(String(400), default="")
    urgency: Mapped[str] = mapped_column(String(400), default="")
    history: Mapped[str] = mapped_column(String(400), default="")
    client_risk: Mapped[str] = mapped_column(String(400), default="")
    tone: Mapped[str] = mapped_column(String(400), default="")
    draft_reply: Mapped[str] = mapped_column(Text, default="")
    actions: Mapped[list | None] = mapped_column(JSON, default=list)
    duplicate_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    model: Mapped[str] = mapped_column(String(64), default="")
    # Что человек с этим разбором сделал. Пока пусто — разбор показывается
    # целиком; как только отработали, схлопывается в одну строку.
    used_for: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="analysis")


class KnowledgeKind(str, enum.Enum):
    architecture = "architecture"  # как устроено, из чего состоит
    howto = "howto"  # как это работает/настраивается
    known_issue = "known_issue"  # типичная поломка и куда смотреть
    solution = "solution"  # что реально помогло в прошлом


class Knowledge(Base):
    """База знаний о системе — то, по чему агент понимает «что да как».

    Заполняется из репозиториев SmartBooking: что за модуль, где живёт, как
    работает, где типичные точки отказа. Источник правды — markdown-файлы в
    `app/knowledge/` (под гитом, их видно и правит человек), а эта таблица —
    их загруженная копия для выдачи агенту. Поэтому `slug` стабильный: загрузчик
    переписывает запись на месте, а не плодит дубли при каждом прогоне.

    Разнесение по `module` — не каталогизация ради каталогизации: диагност
    тянет в промпт знания ровно по модулю обращения, а не всю базу, иначе она
    не влезет и утопит нужное в постороннем.
    """

    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    module: Mapped[Module] = mapped_column(_enum(Module), default=Module.unknown, index=True)
    kind: Mapped[KnowledgeKind] = mapped_column(_enum(KnowledgeKind), default=KnowledgeKind.architecture)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    # Откуда факт: репозитории и ключевые файлы. Чтобы знание можно было
    # перепроверить по коду, а не верить ему на слово.
    source: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Diagnosis(Base):
    """Технический разбор обращения для менеджера: причина и следующий шаг.

    Отдельно от `Analysis`: тот обращён к клиенту (кто написал, тон, черновик
    ответа), а это — к техменеджеру (с чем связано, вероятная причина, что
    делать, кого привлечь). Смешать их в одной сущности — значит показывать
    инженеру тон письма, а руководителю поддержки — гипотезу о коде.

    Диагноз — гипотеза, а не приговор: `probable_cause` намеренно назван
    «вероятной» причиной, а не «причиной». Агент предлагает, решает человек —
    тот же инвариант, что и у предложений разбора. Поэтому диагноз ничего не
    назначает и никому не пишет сам: `suggested_assignee_id` — это подсказка,
    а не факт назначения.
    """

    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Диагноз ставится обращению (чаще) или задаче. Обе ссылки necessary nullable:
    # обращение может ещё не стать задачей, а задачу могли завести без обращения.
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    summary: Mapped[str] = mapped_column(String(400), default="")  # суть одной строкой
    probable_cause: Mapped[str] = mapped_column(Text, default="")  # гипотеза о причине
    suggested_step: Mapped[str] = mapped_column(Text, default="")  # что делать дальше
    # С чем связано: [{"ref": "SB-101", "why": "та же поломка синка Ostrovok"}].
    # Факты (ссылки на реальные задачи) и объяснение модели в одном месте, но
    # ref проверяется кодом — выдуманную задачу в список не пускаем.
    related: Mapped[list | None] = mapped_column(JSON, default=list)
    suggested_module: Mapped[Module] = mapped_column(_enum(Module), default=Module.unknown)
    suggested_assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )

    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    suggested_assignee: Mapped[Staff | None] = relationship()


# --------------------------------------------------------------------------
# Предложения модели
# --------------------------------------------------------------------------


class Proposal(Base):
    """Разбор одного сообщения: новый запрос или продолжение существующей задачи.

    Хранит не только вывод, но и то, как модель к нему пришла (`reading`) и
    что предлагает сделать (`action`). Человек нажимает кнопку, глядя на
    рассуждение, а не на голый вердикт — иначе доверие к боту держится на
    вере, а не на проверке.
    """

    __tablename__ = "proposals"
    __table_args__ = (Index("ix_proposal_queue", "state", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id"), unique=True, index=True
    )
    kind: Mapped[ProposalKind] = mapped_column(_enum(ProposalKind), index=True)
    state: Mapped[ProposalState] = mapped_column(
        _enum(ProposalState), default=ProposalState.pending, index=True
    )

    # Для продолжения — к какой задаче относится.
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Чем именно модель связала сообщение с задачей: reply | mention | semantic.
    # Первые два надёжны, третий ошибается — и это должно быть видно человеку.
    link_basis: Mapped[str] = mapped_column(String(16), default="")

    reading: Mapped[str] = mapped_column(Text, default="")  # «бот понял так»
    action: Mapped[str] = mapped_column(Text, default="")  # «предлагает»
    # Черновик задачи для kind=new_task: заголовок, критерий готовности, метки.
    draft: Mapped[dict | None] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    model: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(160), default="")

    message: Mapped[Message] = relationship(back_populates="proposal")
    task: Mapped[Task | None] = relationship()


class ProposalFeedback(Base):
    """Отклонённое предложение.

    Отказ — это данные, а не просто закрытие карточки: по нему видно, на чём
    модель ошибается систематически, и он подмешивается в промпт как пример.
    """

    __tablename__ = "proposal_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    message_text: Mapped[str] = mapped_column(Text)
    was_kind: Mapped[str] = mapped_column(String(20))
    correct_kind: Mapped[str] = mapped_column(String(20), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --------------------------------------------------------------------------
# Эскалация
# --------------------------------------------------------------------------


class Rule(Base):
    """Правило эскалации — единственное, что срабатывает без человека.

    Выключенное правило не молчит наполовину: нарушение всё равно попадает в
    отчёт, просто никого не будит. Это разные вещи, и путать их нельзя.
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    params: Mapped[dict | None] = mapped_column(JSON, default=dict)
    hits_7d: Mapped[int] = mapped_column(Integer, default=0)


class Escalation(Base):
    """Факт срабатывания правила. Нужен, чтобы не будить одного и того же
    человека дважды по одному поводу."""

    __tablename__ = "escalations"
    __table_args__ = (
        Index("ix_escalation_dedup", "rule_code", "subject_kind", "subject_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(48), index=True)
    subject_kind: Mapped[str] = mapped_column(String(16))  # task | ticket | hotel
    subject_id: Mapped[int] = mapped_column(Integer)
    notified_tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentRun(Base):
    """Журнал вызовов модели — видно, сколько стоит разбор и где он падает."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
