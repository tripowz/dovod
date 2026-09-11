import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Me, TaskFull, TaskRow, TaskStatus } from "../lib/types";
import {
  Card,
  Empty,
  Heat,
  ModuleChip,
  Priority,
  SectionHead,
  TypeChip,
  plural,
} from "../components/ui";

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

  if (open) return <Detail task={open} me={me} onBack={() => setOpen(null)} onStatus={setStatus} />;
  if (!rows) return <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>;

  return (
    <div>
      <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1">
        {SCOPES.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setScope(key)}
            className={`shrink-0 rounded-full border px-3 py-1.5 text-[12px] font-semibold ${
              scope === key
                ? "border-ink bg-ink text-bg"
                : "border-black/8 bg-bg text-ink/70"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <Empty title="Здесь пусто">По этому фильтру задач нет — и это хорошая новость.</Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.map((t) => (
            <Card key={t.ref} onClick={() => void openTask(t.ref)}>
              <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                <TypeChip type={t.type} />
                <ModuleChip module={t.module} />
                <Priority value={t.priority} />
                {t.overdue && (
                  <span className="rounded bg-clay/12 px-2 py-0.5 text-[10.5px] font-bold text-clay">
                    срок прошёл
                  </span>
                )}
              </div>
              <p className="m-0 text-[14px] font-semibold leading-snug">{t.title}</p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted">
                <b className="font-semibold text-ink/70">{t.assignee ?? "не назначен"}</b>
                {t.hotel && <span>· {t.hotel}</span>}
                {t.notes_count > 0 && (
                  <span>
                    · {t.notes_count} {plural(t.notes_count, ["заметка", "заметки", "заметок"])}
                  </span>
                )}
              </div>
              <Heat days={t.idle_days} overdue={t.overdue} />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function Detail({
  task,
  me,
  onBack,
  onStatus,
}: {
  task: TaskFull;
  me: Me;
  onBack: () => void;
  onStatus: (ref: string, s: TaskStatus) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <button onClick={onBack} className="self-start text-[13px] font-semibold text-link">
        ‹ К списку
      </button>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-surface px-2 py-0.5 font-mono text-[11px] font-bold">
          {task.ref}
        </span>
        <TypeChip type={task.type} />
        <ModuleChip module={task.module} />
        <Priority value={task.priority} />
      </div>

      <h2 className="m-0 text-[17px] font-bold leading-tight">{task.title}</h2>
      <div className="text-[11.5px] text-muted">
        {task.assignee ?? "не назначен"} · автор — {task.author ?? "неизвестен"}
        {task.due_at && ` · до ${new Date(task.due_at).toLocaleDateString("ru-RU")}`}
      </div>
      <Heat days={task.idle_days} overdue={task.overdue} />

      <div>
        <SectionHead title="Статус" />
        <div className="grid grid-cols-4 gap-1 rounded-xl bg-surface p-1">
          {STATUS.map((s) => {
            // «Готово» ставит только автор — это и есть механизм против
            // «сделал и забыл». Кнопка гасится, а не молча ничего не делает.
            const blocked = s.key === "done" && !task.can_accept;
            return (
              <button
                key={s.key}
                disabled={blocked}
                onClick={() => onStatus(task.ref, s.key)}
                className={`rounded-lg px-1 py-1.5 text-[10.5px] font-semibold leading-tight disabled:opacity-40 ${
                  task.status === s.key ? "bg-bg text-ink shadow-sm" : "text-muted"
                }`}
              >
                {s.label}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-[11px] leading-snug text-muted">
          {task.can_accept
            ? "Вы автор этой задачи — принимать работу вам."
            : `Автор задачи — ${task.author ?? "другой человек"}. Вы можете сдать работу на проверку, но принять её может только автор.`}
        </p>
      </div>

      <div>
        <SectionHead title="Критерий готовности" />
        <div className="rounded-xl bg-surface px-3 py-2.5 text-[12.5px] leading-snug text-ink/80">
          {task.dod}
        </div>
      </div>

      {task.notes.length > 0 && (
        <div>
          <SectionHead title={`Заметки · ${task.notes.length}`} />
          <div className="flex flex-col gap-2">
            {task.notes.map((n, i) => (
              <div
                key={i}
                className={`rounded-xl px-3 py-2.5 ${
                  n.role === "manager"
                    ? "border-l-[2.5px] border-link bg-link/8"
                    : "border-l-[2.5px] border-black/10 bg-surface"
                }`}
              >
                <div className="mb-1 flex items-center gap-2">
                  <b className="text-[12px] font-semibold">{n.author}</b>
                  <span className="rounded border border-black/8 bg-bg px-1.5 text-[9.5px] font-bold uppercase tracking-wider text-muted">
                    {n.role === "manager" ? "руководитель" : "исполнитель"}
                  </span>
                </div>
                <p className="m-0 text-[12.5px] leading-snug text-ink/75">{n.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {task.thread.length > 0 && (
        <div>
          <SectionHead title={`Нить из чатов · ${task.thread.length}`} />
          <div className="flex flex-col gap-2.5">
            {task.thread.map((m, i) => (
              <div key={i} className="text-[12.5px]">
                <div className="mb-0.5 flex items-center gap-2">
                  <b className="text-[12px] font-semibold">{m.author ?? "—"}</b>
                  <span className="rounded bg-surface px-1.5 text-[10px] text-muted">{m.tag}</span>
                  <time className="ml-auto font-mono text-[10px] text-muted">
                    {new Date(m.sent_at).toLocaleDateString("ru-RU")}
                  </time>
                </div>
                <p className="m-0 leading-snug text-ink/75">{m.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <SectionHead title="История" />
        <div className="flex flex-col gap-2">
          {task.events.map((e, i) => (
            <div key={i} className="flex gap-2.5 text-[12.5px]">
              <i
                className={`mt-1.5 block h-2 w-2 shrink-0 rounded-full ${
                  e.pending ? "border border-muted/50" : "bg-link"
                }`}
              />
              <div>
                <b className="font-semibold">{e.text}</b>
                <time className="block font-mono text-[10.5px] text-muted">
                  {e.pending ? "ожидание" : new Date(e.at).toLocaleString("ru-RU")}
                </time>
              </div>
            </div>
          ))}
        </div>
      </div>

      <p className="pb-4 text-[11px] leading-snug text-muted">
        Задачу можно изменить, объединить с дублем или отменить как ошибочно созданную —
        тогда сообщения вернутся в разбор, а не исчезнут.
      </p>
      {me.is_manager && <div className="h-1" />}
    </div>
  );
}
