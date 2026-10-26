import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Inbox as InboxData, Proposal, TaskType } from "../lib/types";
import {
  Back,
  Badge,
  Button,
  Card,
  Choice,
  Empty,
  FieldLabel,
  Loading,
  Segmented,
  Tag,
  TYPE_LABEL,
  plural,
} from "../components/ui";

const KIND_LABEL: Record<string, string> = {
  new_task: "новый запрос",
  progress: "работа сделана",
  deadline: "перенос срока",
  scope: "запрос вырос",
  duplicate: "то же самое",
  question: "вопрос без ответа",
};

const KIND_CLASS: Record<string, string> = {
  progress: "bg-link/12 text-link",
  deadline: "bg-ochre/15 text-ochre",
  scope: "bg-link/12 text-link",
  duplicate: "bg-link/12 text-link",
  question: "bg-fill text-muted",
};

/** На чём основана связь с задачей. Реплай и упоминание надёжны, смысловая
 *  близость ошибается — и человек должен видеть это до того, как нажмёт. */
const BASIS_LABEL: Record<string, string> = {
  reply: "связано реплаем",
  mention: "связано упоминанием",
  semantic: "связано по смыслу — проверьте",
};

const MODULES = ["PMS", "CM", "WEBSITE", "BOOKING", "MOBILE", "BILLING", "API", "ADMIN"];
const TYPES: TaskType[] = ["bug", "problem", "request", "question"];
const DUES: [number, string][] = [
  [0, "Сегодня"],
  [1, "Завтра"],
  [3, "Через 3 дня"],
  [7, "Через неделю"],
];

/** Срок — 18:00 выбранного дня по местному времени, отправленный наивным UTC:
 *  бэкенд хранит время без зоны, в UTC. */
function dueAt(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(18, 0, 0, 0);
  return d.toISOString().slice(0, 19);
}

interface StaffRow {
  id: number;
  name: string;
  is_manager: boolean;
}

interface Form {
  p: Proposal;
  title: string;
  dod: string;
  type: TaskType;
  module: string;
  assignee: number | null;
  due: number;
}

export default function Inbox({
  guard,
}: {
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [data, setData] = useState<InboxData | null>(null);
  const [seg, setSeg] = useState<"new" | "upd">("new");
  const [form, setForm] = useState<Form | null>(null);
  const [staff, setStaff] = useState<StaffRow[]>([]);

  const load = () => api.get<InboxData>("/inbox").then(setData);
  useEffect(() => {
    void load();
    void api.get<StaffRow[]>("/staff").then(setStaff);
  }, []);

  const openForm = (p: Proposal) => {
    haptic("medium");
    const d = p.draft as Record<string, unknown>;
    setForm({
      p,
      title: String(d.title ?? ""),
      dod: String(d.dod ?? ""),
      type: TYPES.includes(d.type as TaskType) ? (d.type as TaskType) : "request",
      module: MODULES.includes(String(d.module)) ? String(d.module) : "PMS",
      // Ответственного не угадываем: «один ответственный» — решение человека,
      // а подставленное по умолчанию имя легко пропустить не глядя.
      assignee: null,
      due: 1,
    });
  };

  const apply = (p: Proposal) =>
    guard(async () => {
      haptic("medium");
      const r = await api.post<{ message: string }>(`/proposals/${p.id}/apply`);
      await load();
      return r.message;
    });

  const reject = (p: Proposal) =>
    guard(async () => {
      haptic();
      const r = await api.post<{ message: string }>(`/proposals/${p.id}/reject`);
      await load();
      return r.message;
    });

  if (form) {
    const valid =
      form.title.trim().length >= 6 && form.dod.trim().length >= 9 && form.assignee != null;
    const submit = () =>
      guard(async () => {
        const r = await api.post<{ message: string }>("/tasks", {
          title: form.title.trim(),
          dod: form.dod.trim(),
          type: form.type,
          module: form.module,
          assignee_id: form.assignee,
          due_at: dueAt(form.due),
          proposal_id: form.p.id,
        });
        setForm(null);
        await load();
        return r.message;
      });

    const pick = <K,>(label: React.ReactNode, items: [K, string][], cur: K, set: (k: K) => void, mono = false) => (
      <div>
        <FieldLabel>{label}</FieldLabel>
        <div className="flex flex-wrap gap-2">
          {items.map(([k, l]) => (
            <Choice
              key={String(k)}
              on={cur === k}
              onClick={() => set(k)}
              className={mono ? "font-mono !text-[12.5px]" : ""}
            >
              {l}
            </Choice>
          ))}
        </div>
      </div>
    );

    return (
      <div className="flex flex-col gap-5 pb-4">
        <div className="flex flex-col gap-3">
          <Back onClick={() => setForm(null)} />
          <h2 className="m-0 text-[22px] font-bold tracking-[-0.01em]">Новая задача</h2>
        </div>

        <Card>
          <div className="mb-1 text-[13px] font-semibold text-link">
            {form.p.author} · {form.p.chat}
          </div>
          <p className="m-0 text-[15px] leading-snug">{form.p.text}</p>
        </Card>

        <label className="block">
          <FieldLabel>Что сделать</FieldLabel>
          <textarea
            rows={2}
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            className="field !bg-bg"
          />
        </label>

        {pick(
          <>
            Тип <span className="text-ochre">— предложил бот</span>
          </>,
          TYPES.map((t) => [t, TYPE_LABEL[t]] as [TaskType, string]),
          form.type,
          (type) => setForm({ ...form, type }),
        )}

        {pick(
          "Часть системы",
          MODULES.map((m) => [m, m] as [string, string]),
          form.module,
          (module) => setForm({ ...form, module }),
          true,
        )}

        {pick(
          <>
            Ответственный <span className="text-ochre">— один</span>
          </>,
          staff.map((x) => [x.id, x.name] as [number | null, string]),
          form.assignee,
          (assignee) => setForm({ ...form, assignee }),
        )}

        {pick("Срок", DUES, form.due, (due) => setForm({ ...form, due }))}

        <label className="block">
          <FieldLabel>
            Критерий готовности <span className="text-clay">— обязателен</span>
          </FieldLabel>
          <textarea
            rows={3}
            value={form.dod}
            onChange={(e) => setForm({ ...form, dod: e.target.value })}
            className="field !bg-bg"
          />
          <span className="mt-1.5 block px-1 text-[12.5px] leading-snug text-muted">
            По этой фразе автор решит, принимать работу или вернуть. Без неё задачу создать нельзя.
          </span>
        </label>

        <Button className="w-full !py-3.5 !text-[17px]" disabled={!valid} onClick={() => void submit()}>
          {valid ? "Создать задачу" : "Заполните обязательные поля"}
        </Button>
      </div>
    );
  }

  if (!data) return <Loading />;

  const list = seg === "new" ? data.new : data.updates;

  return (
    <div>
      <Segmented
        value={seg}
        onChange={setSeg}
        options={[
          { key: "new", label: <>Новые запросы <Badge n={data.new.length} /></> },
          { key: "upd", label: <>Продолжения <Badge n={data.updates.length} /></> },
        ]}
      />

      <p className="mx-4 mb-3 mt-2 text-[13px] leading-snug text-muted">
        Бот разобрал <b className="font-semibold text-ink/75">{data.scanned}</b>{" "}
        {plural(data.scanned, ["сообщение", "сообщения", "сообщений"])}, отсеял{" "}
        <b className="font-semibold text-ink/75">{data.filtered}</b> как разговоры.
      </p>

      {list.length === 0 ? (
        <Empty title={seg === "new" ? "Новых запросов нет" : "Продолжений нет"}>
          {seg === "new"
            ? "Всё, что бот счёл задачей, уже оформлено или отклонено."
            : "Всё, что бот услышал по текущим задачам, уже применено к карточкам."}
        </Empty>
      ) : (
        <div className="flex flex-col gap-3">
          {list.map((p) => (
            <Card key={p.id}>
              <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
                <Tag
                  className={
                    p.orphan ? "bg-clay/12 text-clay" : (KIND_CLASS[p.kind] ?? "bg-link/12 text-link")
                  }
                >
                  {p.orphan ? "задачи больше нет" : (KIND_LABEL[p.kind] ?? p.kind)}
                </Tag>
                {p.kind === "new_task" && (
                  <Tag className="bg-fill text-muted">{Math.round(p.confidence * 100)}%</Tag>
                )}
                <span className="ml-auto truncate text-[12px] text-muted">{p.chat}</span>
              </div>

              {p.task_ref && (
                <div className="mb-2.5 flex items-center gap-2 rounded-[10px] bg-fill px-3 py-2 text-[14px]">
                  <span className="font-mono text-[12.5px] font-semibold text-link">{p.task_ref}</span>
                  <span className="min-w-0 flex-1 truncate text-ink/75">
                    {p.orphan ? "объединена или отменена" : p.task_title}
                  </span>
                </div>
              )}

              <div className="rounded-[12px] rounded-tl-[4px] bg-fill px-3 py-2.5">
                <div className="mb-0.5 flex items-center gap-2 text-[13px] font-semibold text-link">
                  {p.author}
                  <time className="ml-auto text-[12px] font-normal text-muted">
                    {new Date(p.sent_at).toLocaleString("ru-RU", {
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>
                <p className="m-0 text-[15px] leading-snug">{p.text}</p>
              </div>

              {p.reading && (
                <div className="mt-3 text-[14px] leading-snug text-ink/75">
                  <span className="mb-0.5 block text-[12px] font-medium text-muted">
                    Бот понял так
                  </span>
                  {p.reading}
                </div>
              )}
              {p.action && (
                <div className="mt-2.5 text-[14px] leading-snug">
                  <span className="mb-0.5 block text-[12px] font-medium text-link">
                    Предлагает
                  </span>
                  {p.action}
                </div>
              )}
              {p.link_basis && BASIS_LABEL[p.link_basis] && (
                <div className="mt-2 text-[12px] text-muted">{BASIS_LABEL[p.link_basis]}</div>
              )}

              <div className="mt-3.5 flex gap-2">
                <Button
                  className="flex-1"
                  onClick={() => (p.kind === "new_task" ? openForm(p) : void apply(p))}
                >
                  {p.orphan ? "Вернуть в новые запросы" : p.kind === "new_task" ? "В задачи" : "Применить"}
                </Button>
                <Button variant="ghost" onClick={() => void reject(p)}>
                  Не то
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
