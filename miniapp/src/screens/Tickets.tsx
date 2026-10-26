import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { TicketRow } from "../lib/types";
import { Button, Card, Empty, Loading, ModuleChip, Note, Tag, TypeChip } from "../components/ui";

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

  if (!rows) return <Loading />;
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
      <Note className="!mt-0 mb-3">
        Бот слушает чат поддержки и разбирает каждое обращение в момент отправки.
      </Note>
      <div className="flex flex-col gap-3">
        {rows.map((t) => {
          const late = t.sla_left != null && t.sla_left <= 0;
          const soon = t.sla_left != null && t.sla_left > 0 && t.sla_left <= 15;
          const slaColor = late || soon ? "text-clay" : "text-muted";
          return (
            <Card key={t.ref}>
              <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
                <Tag className="bg-fill font-mono text-ink">{t.ref}</Tag>
                <TypeChip type={t.type} />
                <ModuleChip module={t.module} />
                <Tag className="ml-auto bg-fill text-ink/70">{STATUS_LABEL[t.status] ?? t.status}</Tag>
              </div>

              <div className="text-[17px] font-semibold leading-snug">{t.hotel}</div>
              <div className="text-[13px] text-muted">
                {t.city} · {t.author}
                {t.role && `, ${t.role}`}
              </div>

              <p className="mb-2.5 mt-2 text-[15px] leading-snug text-ink/85">{t.text}</p>

              {t.sla_left != null && (
                <div
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] font-medium ${
                    late || soon ? "bg-clay/10" : "bg-fill"
                  } ${slaColor}`}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 7v5l3 2" />
                  </svg>
                  {late
                    ? `обещание нарушено ${-t.sla_left} мин назад`
                    : `${t.sla_left} мин ${t.awaiting_reply ? "до первого ответа" : "до решения"}`}
                </div>
              )}

              {/* Разбор показывается целиком, пока по нему не отработали.
                  После — схлопывается: держать его открытым значит дублировать
                  ссылки и засорять экран. */}
              {t.analysis && !t.analysis.used_for && (
                <div className="mt-3 rounded-[12px] bg-link/8 p-3">
                  <div className="mb-2 flex items-center gap-1.5">
                    <b className="text-[14px] font-semibold text-link">Разбор ИИ</b>
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
                        <div key={k} className="grid grid-cols-[76px_1fr] gap-2">
                          <dt className="text-[12.5px] text-link/80">
                            {k}
                          </dt>
                          <dd className="m-0 text-[14px] leading-snug">{v}</dd>
                        </div>
                      ))}
                  </dl>

                  {t.analysis.draft_reply && (
                    <div className="mt-3 rounded-[10px] bg-bg px-3 py-2.5">
                      <span className="mb-0.5 block text-[12px] font-medium text-muted">
                        Черновик ответа
                      </span>
                      <p className="m-0 text-[14px] leading-snug">{t.analysis.draft_reply}</p>
                    </div>
                  )}

                  {t.analysis.actions.length > 0 && (
                    <ol className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
                      {t.analysis.actions.map((a, i) => (
                        <li key={i} className="flex items-start gap-2 text-[14px] leading-snug">
                          <span className="mt-px grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full bg-accent text-[11px] font-semibold text-onAccent">
                            {i + 1}
                          </span>
                          {a}
                        </li>
                      ))}
                    </ol>
                  )}

                  <p className="m-0 mt-3 text-[12px] leading-snug text-link/80">
                    Ответ предложен моделью. Клиенту ничего не уходит, пока вы не нажали.
                  </p>
                </div>
              )}

              {t.analysis?.used_for && (
                <div className="mt-3 rounded-[10px] bg-link/8 px-3 py-2 text-[13px] text-link">
                  Разбор ИИ отработан: {t.analysis.used_for}
                </div>
              )}

              {/* Технический диагноз для менеджера: причина, с чем связано и что
                  делать. Отдельно от «Разбора ИИ» (тот обращён к клиенту) — и
                  тоном другой: ochre, не link. */}
              {t.diagnosis && (
                <div className="mt-3 rounded-[12px] bg-ochre/10 p-3">
                  <div className="mb-2 flex items-center gap-1.5">
                    <b className="text-[14px] font-semibold text-ochre">Диагноз · техменеджеру</b>
                    <span className="ml-auto text-[12px] text-muted">
                      уверенность {Math.round(t.diagnosis.confidence * 100)}%
                    </span>
                  </div>

                  {t.diagnosis.summary && (
                    <p className="m-0 text-[15px] font-semibold leading-snug">
                      {t.diagnosis.summary}
                    </p>
                  )}

                  {t.diagnosis.probable_cause && (
                    <div className="mt-2.5">
                      <span className="block text-[12px] font-medium text-ochre">
                        Вероятная причина
                      </span>
                      <p className="m-0 mt-0.5 text-[14px] leading-snug">
                        {t.diagnosis.probable_cause}
                      </p>
                    </div>
                  )}

                  {t.diagnosis.related.length > 0 && (
                    <div className="mt-2.5">
                      <span className="block text-[12px] font-medium text-ochre">
                        С чем связано
                      </span>
                      <div className="mt-1 flex flex-col gap-1">
                        {t.diagnosis.related.map((r) => (
                          <div key={r.ref} className="flex items-start gap-2 text-[14px] leading-snug">
                            <Tag className="mt-px shrink-0 bg-bg font-mono">{r.ref}</Tag>
                            <span className="text-ink/80">{r.why}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {t.diagnosis.suggested_step && (
                    <div className="mt-3 rounded-[10px] bg-bg px-3 py-2.5">
                      <span className="mb-0.5 block text-[12px] font-medium text-muted">
                        Что делать
                      </span>
                      <p className="m-0 whitespace-pre-line text-[14px] leading-snug">
                        {t.diagnosis.suggested_step}
                      </p>
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink/60">
                    <span>модуль {t.diagnosis.suggested_module}</span>
                    {t.diagnosis.suggested_assignee && <span>· кому: {t.diagnosis.suggested_assignee}</span>}
                    <span className="ml-auto">гипотеза ИИ, ничего не назначено</span>
                  </div>

                  {/* Оценка диагноза: мерило качества агента и обучающий сигнал. */}
                  {t.diagnosis.verdict === "pending" ? (
                    <div className="mt-3 border-t-[0.5px] border-ochre/30 pt-3">
                      {wrongFor === t.ref ? (
                        <div>
                          <textarea
                            value={wrongNote}
                            onChange={(e) => setWrongNote(e.target.value)}
                            rows={2}
                            placeholder="Что в диагнозе не так и как правильно — агент это учтёт"
                            className="field !bg-bg"
                          />
                          <div className="mt-2 flex gap-2">
                            <button
                              disabled={wrongNote.trim().length < 3}
                              onClick={() => void verdict(t, "wrong", wrongNote.trim())}
                              className="tap rounded-[10px] bg-clay px-3.5 py-2 text-[14px] font-semibold text-white disabled:opacity-40"
                            >
                              Отправить
                            </button>
                            <button
                              onClick={() => {
                                setWrongFor(null);
                                setWrongNote("");
                              }}
                              className="tap rounded-[10px] bg-bg px-3.5 py-2 text-[14px] font-semibold text-ink/70"
                            >
                              Отмена
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          <span className="text-[14px] text-ink/70">Диагноз точный?</span>
                          <button
                            onClick={() => void verdict(t, "correct")}
                            className="tap ml-auto rounded-[10px] bg-moss/12 px-3.5 py-2 text-[14px] font-semibold text-moss"
                          >
                            Верный
                          </button>
                          <button
                            onClick={() => {
                              setWrongFor(t.ref);
                              setWrongNote("");
                            }}
                            className="tap rounded-[10px] bg-clay/12 px-3.5 py-2 text-[14px] font-semibold text-clay"
                          >
                            Мимо
                          </button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="mt-3 border-t-[0.5px] border-ochre/30 pt-2.5 text-[13px]">
                      {t.diagnosis.verdict === "correct" ? (
                        <span className="font-semibold text-moss">
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
                <div className="mt-3.5 flex flex-wrap gap-2">
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
