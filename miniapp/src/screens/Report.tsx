import { useEffect, useState } from "react";
import { api } from "../lib/tg";
import type { DigestOut, Me } from "../lib/types";
import { Card, Empty, SectionHead, money } from "../components/ui";

/**
 * Сводка руководителя.
 *
 * Отвечает на три вопроса и на этом заканчивается: что горит, кто перегружен,
 * какого клиента можно потерять. Приложение открывается, чтобы что-то
 * сделать; узнать, что происходит, руководитель должен не открывая его —
 * поэтому внизу лежит тот же текст, который приходит сообщением в девять утра.
 */
export default function Report({ me }: { me: Me }) {
  const [d, setD] = useState<DigestOut | null>(null);

  useEffect(() => {
    void api.get<DigestOut>("/digest").then(setD);
  }, []);

  if (!d) return <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>;

  const overloaded = d.load.filter((w) => w.overloaded);
  const slipping = d.load.filter((w) => w.overdue > 0);

  return (
    <div>
      <div className="mb-1 flex items-center gap-3 rounded-2xl bg-ink px-3.5 py-3 text-bg">
        <div>
          <b className="block text-[15px] font-bold">Утро, {me.name}</b>
          <span className="block text-[11px] opacity-70">
            {new Date().toLocaleDateString("ru-RU", {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </span>
        </div>
        <span className="ml-auto font-mono text-[26px] font-bold leading-none">
          {d.fire.length}
        </span>
      </div>

      <SectionHead title="Горит прямо сейчас" right={d.fire.length} />
      {d.fire.length === 0 ? (
        <Empty title="Ничего не горит">
          Ни одного нарушенного обещания и ни одной просрочки.
        </Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {d.fire.map((f, i) => (
            <Card key={i} className="!p-2.5">
              <div className="flex items-start gap-2">
                <i
                  className={`mt-1.5 block h-1.5 w-1.5 shrink-0 rounded-full ${
                    f.severity === "bad" ? "bg-clay" : "bg-ochre"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className="m-0 text-[13px] font-semibold leading-snug">{f.title}</p>
                  <div className="mt-0.5 text-[11px] text-muted">
                    <span className="font-mono">{f.ref}</span> · {f.detail}
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <SectionHead
        title="Нагрузка команды"
        right={overloaded.length ? `${overloaded.length} перегружено` : "ровно"}
      />
      <Card>
        <div className="flex flex-col gap-3">
          {d.load.map((w) => {
            const color = w.overdue ? "bg-clay" : w.overloaded ? "bg-ochre" : "bg-link";
            const pct = Math.min((w.open / w.limit) * 100, 100);
            return (
              <div key={w.name}>
                <div className="mb-1 flex items-baseline gap-2">
                  <b className="text-[13px] font-semibold">{w.name}</b>
                  <span className="ml-auto font-mono text-[11px] text-muted">
                    {w.open} / {w.limit}
                  </span>
                </div>
                <div className="flex h-1.5 overflow-hidden rounded-full bg-surface">
                  <i className={`block h-full ${color}`} style={{ width: `${pct}%` }} />
                </div>
                <div className="mt-1 text-[10.5px] text-muted">
                  {w.overdue > 0 && <span className="text-clay">просрочено {w.overdue} · </span>}
                  {w.idle > 0 && `зависло ${w.idle} · `}
                  {w.review > 0 ? `на проверке ${w.review}` : "на проверке нет"}
                </div>
              </div>
            );
          })}
        </div>
      </Card>
      {/* Перегрузка и срыв срока — разные диагнозы. Человек с одной
          просроченной задачей не перегружен, и лечится это разговором. */}
      {slipping.length > 0 && overloaded.length === 0 && (
        <p className="mt-1.5 px-1 text-[11px] leading-snug text-muted">
          Нагрузка ровная, но срок срывают:{" "}
          {slipping.map((w) => `${w.name} (${w.overdue})`).join(", ")}.
        </p>
      )}

      <SectionHead title="Клиенты, которых можно потерять" right={d.at_risk.length} />
      {d.at_risk.length === 0 ? (
        <Empty title="Все клиенты спокойны">Ни одного отеля в зоне риска.</Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {d.at_risk.map((h) => (
            <Card key={h.name} className="!p-2.5">
              <div className="mb-1 flex items-center gap-2">
                <b className="text-[13px] font-semibold">{h.name}</b>
                <span className="ml-auto font-mono text-[11px] text-muted">
                  {money(h.mrr)} сум/мес
                </span>
              </div>
              <div className="text-[11px] text-muted">
                {h.unanswered > 0 && (
                  <span className="text-clay">без ответа {h.unanswered} · </span>
                )}
                открытых задач {h.open_tasks}
                {h.margin < 0 && <span className="text-clay"> · обслуживание дороже тарифа</span>}
              </div>
              {h.signal && (
                <p className="m-0 mt-1.5 text-[12px] leading-snug text-ink/70">{h.signal}</p>
              )}
            </Card>
          ))}
        </div>
      )}

      <SectionHead title="То же самое приходит в Telegram" />
      <div className="rounded-2xl rounded-bl-sm border border-link bg-link/8 px-3.5 py-3">
        <div className="mb-1.5 flex items-center gap-2">
          <b className="text-[12.5px] font-bold text-link">Довод</b>
          <span className="ml-auto font-mono text-[10px] text-link/70">09:00</span>
        </div>
        <pre className="m-0 whitespace-pre-wrap font-sans text-[12.5px] leading-relaxed">
          {d.telegram_text.replace(/\*/g, "")}
        </pre>
      </div>
      <p className="mb-4 mt-2 px-1 text-[11px] leading-snug text-muted">
        Сводка считается по текущим данным. Сделайте что-нибудь на других вкладках и вернитесь —
        цифры пересчитаются.
      </p>
    </div>
  );
}
