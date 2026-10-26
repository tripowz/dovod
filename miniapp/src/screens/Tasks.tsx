import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Me, TaskFull, TaskRow, TaskStatus } from "../lib/types";
import {
  Back,
  Button,
  Card,
  Choice,
  Empty,
  Group,
  Heat,
  Loading,
  ModuleChip,
  Note,
  Priority,
  Row,
  SectionHead,
  Segmented,
  Tag,
  TypeChip,
  plural,
} from "../components/ui";

/** Чем сообщение в нити является для задачи. Хранится кодом, показывается словом. */
const THREAD_TAG: Record<string, string> = {
  request: "запрос",
  clarification: "уточнение",
  question: "вопрос",
  resolution: "решение",
  postpone: "перенос",
  complaint: "жалоба клиента",
};

const SCOPES = [
  ["all", "Все"],
  ["mine", "Мои"],
  ["stuck", "Зависшие"],
  ["review", "На проверке"],
  ["late", "Просрочено"],
] as const;

/** Статус одной задачи. Отличается от названия колонки на доске: «Новая» про
 *  одну задачу, «Новые» — про много. */
const STATUS: { key: TaskStatus; label: string }[] = [
  { key: "new", label: "Новая" },
  { key: "in_progress", label: "В работе" },
  { key: "review", label: "На проверке" },
  { key: "done", label: "Готово" },
];

export default function Tasks({
  me,
  guard,
}: {
  me: Me;
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [scope, setScope] = useState<string>("all");
  const [open, setOpen] = useState<TaskFull | null>(null);

  const load = () => api.get<TaskRow[]>(`/tasks?scope=${scope}`).then(setRows);
  useEffect(() => {
    void load();
  }, [scope]);

  const openTask = (ref: string) =>
    api.get<TaskFull>(`/tasks/${ref}`).then((t) => {
      haptic();
      setOpen(t);
    });

  const setStatus = (ref: string, status: TaskStatus) =>
    guard(async () => {
      await api.post(`/tasks/${ref}/status`, { status });
      await openTask(ref);
      await load();
      return `Статус: ${STATUS.find((s) => s.key === status)?.label}`;
    });

  const rework = (ref: string, reason: string) =>
    guard(async () => {
      haptic("medium");
      const r = await api.post<{ message: string }>(`/tasks/${ref}/rework`, { reason });
      await openTask(ref);
      await load();
      return r.message;
    });

  if (open)
    return (
      <Detail
        task={open}
        me={me}
        onBack={() => setOpen(null)}
        onStatus={setStatus}
        onRework={rework}
      />
    );
  if (!rows) return <Loading />;

  return (
    <div>
      <div className="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
        {SCOPES.map(([key, label]) => (
          <Choice key={key} on={scope === key} onClick={() => setScope(key)} className="shrink-0">
            {label}
          </Choice>
        ))}
      </div>

      {rows.length === 0 ? (
        <Empty title="Здесь пусто">По этому фильтру задач нет — и это хорошая новость.</Empty>
      ) : (
        <Group>
          {rows.map((t) => (
            <Row key={t.ref} onClick={() => void openTask(t.ref)}>
              <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                <TypeChip type={t.type} />
                <ModuleChip module={t.module} />
                <Priority value={t.priority} />
                {t.overdue && <Tag className="bg-clay/12 text-clay">срок прошёл</Tag>}
              </div>
              <p className="m-0 text-[16px] font-semibold leading-snug">{t.title}</p>
              <div className="mt-1 flex flex-wrap items-center gap-1 text-[13px] text-muted">
                <span className="text-ink/75">{t.assignee ?? "не назначен"}</span>
                {t.hotel && <span>· {t.hotel}</span>}
                {t.notes_count > 0 && (
                  <span>
                    · {t.notes_count} {plural(t.notes_count, ["заметка", "заметки", "заметок"])}
                  </span>
                )}
              </div>
              <Heat days={t.idle_days} overdue={t.overdue} />
            </Row>
          ))}
        </Group>
      )}
    </div>
  );
}

function Detail({
  task,
  me,
  onBack,
  onStatus,
  onRework,
}: {
  task: TaskFull;
  me: Me;
  onBack: () => void;
  onStatus: (ref: string, s: TaskStatus) => void;
  onRework: (ref: string, reason: string) => void;
}) {
  const [reworkOpen, setReworkOpen] = useState(false);
  const [reason, setReason] = useState("");
  // Вернуть с проверки вправе автор (приёмщик) или руководитель — проверка со
  // стороны руководителя и есть смысл возврата.
  const canReturn = task.status === "review" && (task.can_accept || me.is_manager);

  return (
    <div className="flex flex-col">
      <Back onClick={onBack}>Задачи</Back>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Tag className="bg-fill font-mono text-ink">{task.ref}</Tag>
        <TypeChip type={task.type} />
        <ModuleChip module={task.module} />
        <Priority value={task.priority} />
      </div>

      <h2 className="m-0 mt-2 text-[22px] font-bold leading-tight tracking-[-0.01em]">{task.title}</h2>
      <div className="mt-1 text-[14px] text-muted">
        {task.assignee ?? "не назначен"} · автор — {task.author ?? "неизвестен"}
        {task.due_at && ` · до ${new Date(task.due_at).toLocaleDateString("ru-RU")}`}
      </div>
      <Heat days={task.idle_days} overdue={task.overdue} />

      <SectionHead title="Статус" />
      {/* «Готово» ставит только автор — это и есть механизм против
          «сделал и забыл». Кнопка гасится, а не молча ничего не делает. */}
      <Segmented
        value={task.status}
        onChange={(k) => onStatus(task.ref, k)}
        options={STATUS.map((s) => ({
          key: s.key,
          label: <span className="text-[12px]">{s.label}</span>,
          disabled: s.key === "done" && !task.can_accept,
        }))}
      />
      <Note>
        {task.can_accept
          ? "Вы автор этой задачи — принимать работу вам."
          : `Автор задачи — ${task.author ?? "другой человек"}. Вы можете сдать работу на проверку, но принять её может только автор.`}
      </Note>

      {canReturn && (
        <div className="mt-3">
          {!reworkOpen ? (
            <Button variant="danger" className="w-full" onClick={() => setReworkOpen(true)}>
              Вернуть на доработку
            </Button>
          ) : (
            <Card>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                placeholder="Причина возврата: что доделать и почему работа не принята"
                className="field"
              />
              <div className="mt-2 flex gap-2">
                <button
                  disabled={reason.trim().length < 3}
                  onClick={() => {
                    onRework(task.ref, reason.trim());
                    setReworkOpen(false);
                    setReason("");
                  }}
                  className="tap flex-1 rounded-[12px] bg-clay px-3 py-2.5 text-[15px] font-semibold text-white disabled:opacity-40"
                >
                  Отправить возврат
                </button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setReworkOpen(false);
                    setReason("");
                  }}
                >
                  Отмена
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}

      <SectionHead title="Критерий готовности" />
      <Card className="text-[15px] leading-snug">{task.dod}</Card>

      {task.notes.length > 0 && (
        <>
          <SectionHead title="Заметки" right={task.notes.length} />
          <Group>
            {task.notes.map((n, i) => (
              <Row key={i}>
                <div className="mb-0.5 flex items-center gap-2">
                  <b className="text-[14px] font-semibold">{n.author}</b>
                  <Tag className={n.role === "manager" ? "bg-link/12 text-link" : "bg-fill text-muted"}>
                    {n.role === "manager" ? "руководитель" : "исполнитель"}
                  </Tag>
                </div>
                <p className="m-0 text-[14px] leading-snug text-ink/80">{n.text}</p>
              </Row>
            ))}
          </Group>
        </>
      )}

      {task.thread.length > 0 && (
        <>
          <SectionHead title="Нить из чатов" right={task.thread.length} />
          <Group>
            {task.thread.map((m, i) => (
              <Row key={i}>
                <div className="mb-0.5 flex items-center gap-2">
                  <b className="text-[14px] font-semibold">{m.author ?? "—"}</b>
                  <Tag className="bg-fill text-muted">{THREAD_TAG[m.tag] ?? m.tag}</Tag>
                  <time className="ml-auto text-[12px] text-muted">
                    {new Date(m.sent_at).toLocaleDateString("ru-RU")}
                  </time>
                </div>
                <p className="m-0 text-[14px] leading-snug text-ink/80">{m.text}</p>
              </Row>
            ))}
          </Group>
        </>
      )}

      <SectionHead title="История" />
      <Card>
        <div className="flex flex-col">
          {task.events.map((e, i) => (
            <div key={i} className="relative flex gap-3 pb-3 last:pb-0">
              {i < task.events.length - 1 && (
                <span className="absolute bottom-0 left-[4.5px] top-4 w-px bg-sep" />
              )}
              <i
                className={`relative mt-1.5 block h-2.5 w-2.5 shrink-0 rounded-full ${
                  e.pending ? "border-[1.5px] border-muted/60 bg-bg" : "bg-accent"
                }`}
              />
              <div>
                <div className="text-[14px] font-medium leading-snug">{e.text}</div>
                <time className="block text-[12px] text-muted">
                  {e.pending ? "ожидание" : new Date(e.at).toLocaleString("ru-RU")}
                </time>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Note className="pb-4">
        Задачу можно изменить, объединить с дублем или отменить как ошибочно созданную —
        тогда сообщения вернутся в разбор, а не исчезнут.
      </Note>
    </div>
  );
}
