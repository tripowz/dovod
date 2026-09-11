import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Inbox as InboxData, Proposal } from "../lib/types";
import { Button, Card, Empty, plural } from "../components/ui";

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
  deadline: "bg-ochre/12 text-ochre",
  scope: "bg-link/12 text-link",
  duplicate: "bg-link/12 text-link",
  question: "bg-muted/15 text-muted",
};

/** Насколько модель уверена в связи с задачей.
 *  Реплай и упоминание надёжны, смысловая близость ошибается — и человек
 *  должен видеть, на чём основана связь, прежде чем нажать «Применить». */
const BASIS_LABEL: Record<string, string> = {
  reply: "связано реплаем",
  mention: "связано упоминанием",
  semantic: "связано по смыслу — проверьте",
};

export default function Inbox({
  guard,
}: {
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [data, setData] = useState<InboxData | null>(null);
  const [seg, setSeg] = useState<"new" | "upd">("new");

  const load = () => api.get<InboxData>("/inbox").then(setData);
  useEffect(() => {
    void load();
  }, []);

  if (!data) return <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>;

  const reject = (p: Proposal) =>
    guard(async () => {
      haptic();
      const r = await api.post<{ message: string }>(`/proposals/${p.id}/reject`);
      await load();
      return r.message;
    });

  const list = seg === "new" ? data.new : data.updates;

  return (
    <div>
      <div className="mb-2 grid grid-cols-2 gap-1 rounded-xl bg-surface p-1">
        {(
          [
            ["new", "Новые запросы", data.new.length],
            ["upd", "Продолжения", data.updates.length],
          ] as const
        ).map(([key, label, n]) => (
          <button
            key={key}
            onClick={() => setSeg(key)}
            className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[12.5px] font-semibold ${
              seg === key ? "bg-bg text-ink shadow-sm" : "text-muted"
            }`}
          >
            {label}
            {n > 0 && (
              <em className="rounded-lg bg-clay px-1.5 font-mono text-[10px] not-italic text-white">
                {n}
              </em>
            )}
          </button>
        ))}
      </div>

      <div className="px-1 pb-2.5 text-[11.5px] leading-snug text-muted">
        Бот разобрал <b className="font-mono text-ink/70">{data.scanned}</b>{" "}
        {plural(data.scanned, ["сообщение", "сообщения", "сообщений"])}, отсеял{" "}
        <b className="font-mono text-ink/70">{data.filtered}</b> как разговоры.
      </div>

      {list.length === 0 ? (
        <Empty title={seg === "new" ? "Новых запросов нет" : "Продолжений нет"}>
          {seg === "new"
            ? "Всё, что бот счёл задачей, уже оформлено или отклонено."
            : "Всё, что бот услышал по текущим задачам, уже применено к карточкам."}
        </Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {list.map((p) => (
            <Card key={p.id}>
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span
                  className={`rounded px-2 py-0.5 text-[10.5px] font-bold ${
                    p.orphan ? "bg-clay/12 text-clay" : (KIND_CLASS[p.kind] ?? "bg-link/12 text-link")
                  }`}
                >
                  {p.orphan ? "задачи больше нет" : (KIND_LABEL[p.kind] ?? p.kind)}
                </span>
                {p.kind === "new_task" && !p.orphan && (
                  <span className="rounded bg-surface px-2 py-0.5 font-mono text-[10px] text-muted">
                    {Math.round(p.confidence * 100)}%
                  </span>
                )}
                <span className="ml-auto truncate font-mono text-[10.5px] text-muted">
                  {p.chat}
                </span>
              </div>

              {p.task_ref && (
                <div className="mb-2 flex items-center gap-2 rounded-xl border border-black/6 bg-surface px-2.5 py-2 text-[12.5px]">
                  <span className="font-mono text-[11.5px] font-bold text-link">{p.task_ref}</span>
                  <span className="min-w-0 flex-1 truncate text-ink/70">
                    {p.orphan ? "объединена или отменена" : p.task_title}
                  </span>
                </div>
              )}

              <div className="rounded-r-lg border-l-[2.5px] border-link bg-surface px-2.5 py-2">
                <div className="mb-0.5 flex items-center gap-2 text-[11.5px] font-semibold text-link">
                  {p.author}
                  <time className="ml-auto font-mono text-[10.5px] font-normal text-muted">
                    {new Date(p.sent_at).toLocaleString("ru-RU", {
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>
                <p className="m-0 text-[13px] leading-snug">{p.text}</p>
              </div>

              {p.reading && (
                <div className="mt-2.5 border-l-2 border-black/10 pl-3 text-[12.5px] leading-snug text-ink/70">
                  <span className="mb-0.5 block text-[10px] font-bold uppercase tracking-wider text-muted">
                    Бот понял так
                  </span>
                  {p.reading}
                </div>
              )}
              {p.action && (
                <div className="mt-1.5 border-l-2 border-link pl-3 text-[12.5px] leading-snug">
                  <span className="mb-0.5 block text-[10px] font-bold uppercase tracking-wider text-link">
                    Предлагает
                  </span>
                  {p.action}
                </div>
              )}
              {p.link_basis && BASIS_LABEL[p.link_basis] && (
                <div className="mt-1.5 text-[10.5px] text-muted">
                  {BASIS_LABEL[p.link_basis]}
                </div>
              )}

              <div className="mt-3 flex gap-1.5">
                <Button className="flex-1" onClick={() => haptic("medium")}>
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
