/** Типы ответов API. Держатся рядом с бэкендом вручную: генератор схем
 *  добавил бы шаг сборки ради одного приложения. */

export type TaskType = "bug" | "problem" | "request" | "question";
export type TaskStatus = "new" | "in_progress" | "review" | "done" | "merged" | "cancelled";

export interface Me {
  id: number;
  name: string;
  is_manager: boolean;
  wip_limit: number;
}

export interface StaffManage {
  id: number;
  tg_user_id: number;
  name: string;
  is_manager: boolean;
  wip_limit: number;
  active: boolean;
}

export interface TaskRow {
  ref: string;
  title: string;
  dod: string;
  type: TaskType;
  module: string;
  priority: number;
  status: TaskStatus;
  assignee: string | null;
  author: string | null;
  hotel: string | null;
  due_at: string | null;
  idle_days: number;
  overdue: boolean;
  notes_count: number;
}

export interface TaskNote {
  author: string;
  role: "assignee" | "manager";
  text: string;
  created_at: string;
}

export interface TaskEvent {
  text: string;
  actor: string;
  pending: boolean;
  at: string;
}

export interface ThreadMsg {
  author: string | null;
  text: string;
  tag: string;
  sent_at: string;
}

export interface TaskFull extends TaskRow {
  can_accept: boolean;
  estimate_hours: number | null;
  spent_hours: number;
  notes: TaskNote[];
  events: TaskEvent[];
  thread: ThreadMsg[];
}

export interface Proposal {
  id: number;
  kind: "new_task" | "progress" | "deadline" | "scope" | "duplicate" | "question";
  chat: string;
  author: string;
  sent_at: string;
  text: string;
  reading: string;
  action: string;
  confidence: number;
  link_basis: string;
  draft: Record<string, unknown>;
  task_ref: string | null;
  task_title: string | null;
  orphan: boolean;
}

export interface Inbox {
  new: Proposal[];
  updates: Proposal[];
  /** Запросы из догруженной истории: во «Входящие» не идут, лежат архивом. */
  history: Proposal[];
  history_total: number;
  scanned: number;
  filtered: number;
}

export interface Analysis {
  who: string;
  urgency: string;
  history: string;
  client_risk: string;
  tone: string;
  draft_reply: string;
  actions: string[];
  used_for: string;
}

/** Технический диагноз обращения для техменеджера (агент-диагност).
 *  related — ссылки на реальные задачи, уже проверенные бэкендом. */
export interface TicketDiagnosis {
  summary: string;
  probable_cause: string;
  suggested_step: string;
  related: { ref: string; why: string }[];
  suggested_module: string;
  suggested_assignee: string | null;
  confidence: number;
  model: string;
  verdict: "pending" | "correct" | "wrong";
  verdict_note: string;
  verdict_by: string;
}

export interface TicketRow {
  ref: string;
  hotel: string;
  city: string;
  author: string;
  role: string;
  text: string;
  type: TaskType;
  module: string;
  status: string;
  sla_left: number | null;
  awaiting_reply: boolean;
  task_ref: string | null;
  analysis: Analysis | null;
  diagnosis: TicketDiagnosis | null;
}

export interface HotelRow {
  id: number;
  name: string;
  city: string;
  rooms: number;
  tariff: string;
  risk: "low" | "medium" | "high";
  mrr: number;
  cost: number;
  margin: number;
  verdict: string;
  payment_note: string;
  signals: string[];
  offers: { h: string; p: string; c: string }[];
  open_tasks: number;
  unanswered: number;
}

export interface FireItem {
  kind: string;
  ref: string;
  title: string;
  detail: string;
  severity: "bad" | "warn";
}

export interface LoadItem {
  name: string;
  open: number;
  overdue: number;
  idle: number;
  review: number;
  limit: number;
  overloaded: boolean;
}

export interface DigestOut {
  fire: FireItem[];
  load: LoadItem[];
  at_risk: {
    name: string;
    risk: string;
    mrr: number;
    unanswered: number;
    open_tasks: number;
    margin: number;
    signal: string;
  }[];
  telegram_text: string;
}

/** Метрики одного сотрудника за период. Поля повторяют _staff_metrics_payload
 *  в routes.py: null в avg_first_reply_min значит «клиентам не отвечали», а не
 *  «отвечали мгновенно». */
/** Динамика к прошлому такому же периоду. Дельты — числа; что считать хорошим
 *  ростом, решает экран (закрыто вверх — хорошо, возвраты вверх — плохо).
 *  avg_first_reply_min — null, когда сравнивать не с чем. */
export interface StaffTrend {
  done: number;
  resolved_tickets: number;
  reworks: number;
  avg_first_reply_min: number | null;
  has_prev: boolean;
}

export interface StaffMetric {
  staff_id: number;
  name: string;
  done: number;
  resolved_tickets: number;
  avg_first_reply_min: number | null;
  reworks: number;
  rework_rate: number;
  overdue_now: number;
  open_now: number;
  review_now: number;
  spent_hours: number;
  estimate_hours: number;
  estimate_gap_hours: number;
  trend: StaffTrend;
}

export interface StaffReportOut {
  days: number;
  staff: StaffMetric[];
}
