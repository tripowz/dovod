import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { TicketRow } from "../lib/types";
import { Button, Card, Empty, ModuleChip, TypeChip } from "../components/ui";

const STATUS_LABEL: Record<string, string> = {
  new: "Новая",
  working: "Разбираем",
  waiting_client: "Ждём отель",
  waiting_dev: "Ждём разработку",
  resolved: "Решена",
  closed: "Закрыта",
};

export default function Tickets({
  guard,
}: {
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [rows, setRows] = useState<TicketRow[] | null>(null);
  // Какой тикет сейчас диагностируется: диагноз — вызов модели в несколько
  // секунд, и кнопку на это время надо заблокировать, иначе её нажмут дважды.
  const [busy, setBusy] = useState<string | null>(null);
  // Для какого тикета открыто поле причины «мимо» и что в нём набрано.
  const [wrongFor, setWrongFor] = useState<string | null>(null);
  const [wrongNote, setWrongNote] = useState("");
  const load = () => api.get<TicketRow[]>("/tickets").then(setRows);
  useEffect(() => {
    void load();
  }, []);

  if (!rows) return <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>;
  if (rows.length === 0)
    return <Empty title="Обращений нет">Чат поддержки тихий — редкий и хороший день.</Empty>;

  const reply = (t: TicketRow) =>
    guard(async () => {
      haptic("medium");
      const r = await api.post<{ message: string }>(`/tickets/${t.ref}/reply`);
      await load();
      return r.message;
    });

  const resolve = (t: TicketRow) =>
    guard(async () => {
      await api.post(`/tickets/${t.ref}/resolve`);
      await load();
      return `${t.ref} решена`;
    });

  const diagnose = (t: TicketRow) =>
    guard(async () => {
      haptic("medium");
      setBusy(t.ref);
      try {
        await api.post(`/tickets/${t.ref}/diagnose`);
        await load();
        return `${t.ref}: диагноз готов`;
      } finally {
        setBusy(null);
      }
    });

  const verdict = (t: TicketRow, v: "correct" | "wrong", note = "") =>
    guard(async () => {
      await api.post(`/tickets/${t.ref}/diagnosis/verdict`, { verdict: v, note });
      setWrongFor(null);
      setWrongNote("");
      await load();
      return v === "correct"
        ? `${t.ref}: диагноз отмечен верным`
        : `${t.ref}: отмечено «мимо» — агент учтёт`;
    });

  return (
    <div>
      <div className="px-1 pb-2 text-[11.5px] leading-snug text-muted">
        Бот слушает чат поддержки и разбирает каждое обращение в момент отправки.
      </div>
      <div className="flex flex-col gap-2">
        {rows.map((t) => {
          const late = t.sla_left != null && t.sla_left <= 0;
          const soon = t.sla_left != null && t.sla_left > 0 && t.sla_left <= 15;
          const slaColor = late || soon ? "text-clay" : "text-muted";
          return (
            <Card key={t.ref}>
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-surface px-2 py-0.5 font-mono text-[11px] font-bold">
                  {t.ref}
                </span>
                <TypeChip type={t.type} />
                <ModuleChip module={t.module} />
                <span className="rounded bg-surface px-2 py-0.5 text-[10.5px] font-bold text-ink/70">
                  {STATUS_LABEL[t.status] ?? t.status}
                </span>
              </div>

              <div className="text-[13px] font-semibold">{t.hotel}</div>
              <div className="text-[11px] text-muted">
                {t.city} · {t.author}
                {t.role && `, ${t.role}`}
              </div>

              <p className="mb-2.5 mt-2 text-[13px] leading-snug text-ink/80">{t.text}</p>

              {t.sla_left != null && (
                <div className={`font-mono text-[11px] ${slaColor}`}>
                  {late
                    ? `обещание нарушено ${-t.sla_left} мин назад`
                    : `${t.sla_left} мин ${t.awaiting_reply ? "до первого ответа" : "до решения"}`}
                </div>
              )}

              {/* Разбор показывается целиком, пока по нему не отработали.
                  После — схлопывается: держать его открытым значит дублировать
                  ссылки и засорять экран. */}
              {t.analysis && !t.analysis.used_for && (
                <div className="mt-2.5 rounded-2xl border border-link bg-link/8 p-3">
                  <div className="mb-2 flex items-center gap-1.5">
                    <b className="text-[12px] font-bold text-link">Разбор ИИ</b>
                  </div>
                  <dl className="m-0 flex flex-col gap-1.5">
                    {(
                      [
                        ["Кто пишет", t.analysis.who],
                        ["Срочность", t.analysis.urgency],
                        ["История", t.analysis.history],
                        ["Клиент", t.analysis.client_risk],
                        ["Тон", t.analysis.tone],
                      ] as const
                    )
                      .filter(([, v]) => v)
                      .map(([k, v]) => (
                        <div key={k} className="grid grid-cols-[64px_1fr] gap-2">
                          <dt className="pt-0.5 text-[9.5px] font-bold uppercase tracking-wider text-link/80">
                            {k}
                          </dt>
                          <dd className="m-0 text-[12.5px] leading-snug">{v}</dd>
                        </div>
                      ))}
                  </dl>

                  {t.analysis.draft_reply && (
                    <div className="mt-2.5 rounded-xl bg-bg px-3 py-2.5">
                      <span className="mb-0.5 block text-[10px] font-bold uppercase tracking-wider text-muted">
                        Черновик ответа
                      </span>
                      <p className="m-0 text-[12.5px] leading-snug">{t.analysis.draft_reply}</p>
                    </div>
                  )}

                  {t.analysis.actions.length > 0 && (
                    <ol className="m-0 mt-2.5 flex list-none flex-col gap-1.5 p-0">
                      {t.analysis.actions.map((a, i) => (
                        <li key={i} className="flex items-start gap-2 text-[12px] leading-snug">
                          <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-accent font-mono text-[9.5px] font-bold text-onAccent">
                            {i + 1}
                          </span>
                          {a}
                        </li>
                      ))}
                    </ol>
                  )}

                  <p className="mt-2.5 border-t border-dashed border-link/50 pt-2 text-[10.5px] leading-snug text-link/90">
                    Ответ предложен моделью. Клиенту ничего не уходит, пока вы не нажали.
                  </p>
                </div>
              )}

              {t.analysis?.used_for && (
                <div className="mt-2.5 rounded-xl border border-dashed border-link px-3 py-2 text-[11.5px] text-link">
                  Разбор ИИ отработан: {t.analysis.used_for}
                </div>
              )}

              {/* Технический диагноз для менеджера: причина, с чем связано и что
                  делать. Отдельно от «Разбора ИИ» (тот обращён к клиенту) — и
                  тоном другой: ochre, не link. */}
              {t.diagnosis && (
                <div className="mt-2.5 rounded-2xl border border-ochre/40 bg-ochre/8 p-3">
                  <div className="mb-2 flex items-center gap-1.5">
                    <b className="text-[12px] font-bold text-ochre">Диагноз · техменеджеру</b>
                    <span className="ml-auto font-mono text-[10px] text-muted">
                      уверенность {Math.round(t.diagnosis.confidence * 100)}%
                    </span>
                  </div>

                  {t.diagnosis.summary && (
                    <p className="m-0 text-[13px] font-semibold leading-snug">
                      {t.diagnosis.summary}
                    </p>
                  )}

                  {t.diagnosis.probable_cause && (
                    <div className="mt-2">
                      <span className="block text-[9.5px] font-bold uppercase tracking-wider text-ochre/90">
                        Вероятная причина
                      </span>
                      <p className="m-0 mt-0.5 text-[12.5px] leading-snug">
                        {t.diagnosis.probable_cause}
                      </p>
                    </div>
                  )}

                  {t.diagnosis.related.length > 0 && (
                    <div className="mt-2">
                      <span className="block text-[9.5px] font-bold uppercase tracking-wider text-ochre/90">
                        С чем связано
                      </span>
                      <div className="mt-1 flex flex-col gap-1">
                        {t.diagnosis.related.map((r) => (
                          <div key={r.ref} className="flex items-start gap-1.5 text-[12px] leading-snug">
                            <span className="shrink-0 rounded bg-bg px-1.5 py-0.5 font-mono text-[10.5px] font-bold">
                              {r.ref}
                            </span>
                            <span className="text-ink/80">{r.why}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {t.diagnosis.suggested_step && (
                    <div className="mt-2 rounded-xl bg-bg px-3 py-2.5">
                      <span className="mb-0.5 block text-[10px] font-bold uppercase tracking-wider text-muted">
                        Что делать
                      </span>
                      <p className="m-0 whitespace-pre-line text-[12.5px] leading-snug">
                        {t.diagnosis.suggested_step}
                      </p>
                    </div>
                  )}

                  <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-dashed border-ochre/40 pt-2 text-[10.5px] text-ink/70">
                    <span>модуль {t.diagnosis.suggested_module}</span>
                    {t.diagnosis.suggested_assignee && <span>· кому: {t.diagnosis.suggested_assignee}</span>}
                    <span className="ml-auto">гипотеза ИИ, ничего не назначено</span>
                  </div>

                  {/* Оценка диагноза: мерило качества агента и обучающий сигнал. */}
                  {t.diagnosis.verdict === "pending" ? (
                    <div className="mt-2.5 border-t border-dashed border-ochre/40 pt-2.5">
                      {wrongFor === t.ref ? (
                        <div>
                          <textarea
                            value={wrongNote}
                            onChange={(e) => setWrongNote(e.target.value)}
                            rows={2}
                            placeholder="Что в диагнозе не так и как правильно — агент это учтёт"
                            className="w-full resize-none rounded-lg border border-black/10 bg-bg px-2.5 py-2 text-[12px] leading-snug outline-none"
                          />
                          <div className="mt-1.5 flex gap-1.5">
                            <button
                              disabled={wrongNote.trim().length < 3}
                              onClick={() => void verdict(t, "wrong", wrongNote.trim())}
                              className="rounded-lg bg-clay px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-40"
                            >
                              Отправить
                            </button>
                            <button
                              onClick={() => {
                                setWrongFor(null);
                                setWrongNote("");
                              }}
                              className="rounded-lg bg-surface px-3 py-1.5 text-[12px] font-semibold text-ink/70"
                            >
                              Отмена
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] text-muted">Диагноз точный?</span>
                          <button
                            onClick={() => void verdict(t, "correct")}
                            className="ml-auto rounded-lg bg-link/12 px-3 py-1.5 text-[12px] font-semibold text-link"
                          >
                            Верный
                          </button>
                          <button
                            onClick={() => {
                              setWrongFor(t.ref);
                              setWrongNote("");
                            }}
                            className="rounded-lg bg-clay/12 px-3 py-1.5 text-[12px] font-semibold text-clay"
                          >
                            Мимо
                          </button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="mt-2.5 border-t border-dashed border-ochre/40 pt-2 text-[11px]">
                      {t.diagnosis.verdict === "correct" ? (
                        <span className="font-semibold text-link">
                          ✓ отмечен верным{t.diagnosis.verdict_by && ` · ${t.diagnosis.verdict_by}`}
                        </span>
                      ) : (
                        <span className="text-clay">
                          <b className="font-semibold">✗ мимо</b>
                          {t.diagnosis.verdict_by && ` · ${t.diagnosis.verdict_by}`}
                          {t.diagnosis.verdict_note && `: ${t.diagnosis.verdict_note}`}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {t.status !== "resolved" && t.status !== "closed" && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {t.awaiting_reply && (
                    <Button className="flex-1" onClick={() => void reply(t)}>
                      Ответить в Telegram
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    disabled={busy === t.ref}
                    onClick={() => void diagnose(t)}
                  >
                    {busy === t.ref ? "Думаю…" : t.diagnosis ? "Обновить" : "Диагноз"}
                  </Button>
                  <Button variant="ghost" onClick={() => void resolve(t)}>
                    Решена
                  </Button>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
