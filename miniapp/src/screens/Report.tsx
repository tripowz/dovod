import { useEffect, useState } from "react";
import { api } from "../lib/tg";
import type { DigestOut, Me } from "../lib/types";
import { Card, Empty, Group, Loading, Note, Row, SectionHead, money } from "../components/ui";

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

  if (!d) return <Loading />;

  const overloaded = d.load.filter((w) => w.overloaded);
  const slipping = d.load.filter((w) => w.overdue > 0);

  return (
    <div>
      <div className="flex items-center gap-3 rounded-[14px] bg-bg px-4 py-3.5">
        <div className="min-w-0">
          <b className="block text-[17px] font-semibold">Утро, {me.name}</b>
          <span className="block text-[13px] text-muted">
            {new Date().toLocaleDateString("ru-RU", {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </span>
        </div>
        <div className="ml-auto text-right">
          <span
            className={`block text-[34px] font-bold leading-none tabular-nums ${
              d.fire.length ? "text-clay" : "text-moss"
            }`}
          >
            {d.fire.length}
          </span>
          <span className="text-[11px] text-muted">горит</span>
        </div>
      </div>

      <SectionHead title="Горит прямо сейчас" right={d.fire.length} />
      {d.fire.length === 0 ? (
        <Empty title="Ничего не горит">
          Ни одного нарушенного обещания и ни одной просрочки.
        </Empty>
      ) : (
        <Group>
          {d.fire.map((f, i) => (
            <Row key={i}>
              <div className="flex items-start gap-3">
                <i
                  className={`mt-[7px] block h-2 w-2 shrink-0 rounded-full ${
                    f.severity === "bad" ? "bg-clay" : "bg-ochre"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className="m-0 text-[15px] font-semibold leading-snug">{f.title}</p>
                  <div className="mt-0.5 text-[13px] text-muted">
                    <span className="font-mono">{f.ref}</span> · {f.detail}
                  </div>
                </div>
              </div>
            </Row>
          ))}
        </Group>
      )}

      <SectionHead
        title="Нагрузка команды"
        right={overloaded.length ? `${overloaded.length} перегружено` : "ровно"}
      />
      <Card>
        <div className="flex flex-col gap-4">
          {d.load.map((w) => {
            const color = w.overdue ? "bg-clay" : w.overloaded ? "bg-ochre" : "bg-accent";
            const pct = Math.min((w.open / w.limit) * 100, 100);
            return (
              <div key={w.name}>
                <div className="mb-1.5 flex items-baseline gap-2">
                  <b className="text-[15px] font-semibold">{w.name}</b>
                  <span className="ml-auto text-[13px] tabular-nums text-muted">
                    {w.open} / {w.limit}
                  </span>
                </div>
                <div className="flex h-1.5 overflow-hidden rounded-full bg-fill">
                  <i className={`block h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
                </div>
                <div className="mt-1.5 text-[12.5px] text-muted">
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
        <Note>
          Нагрузка ровная, но срок срывают:{" "}
          {slipping.map((w) => `${w.name} (${w.overdue})`).join(", ")}.
        </Note>
      )}

      <SectionHead title="Клиенты, которых можно потерять" right={d.at_risk.length} />
      {d.at_risk.length === 0 ? (
        <Empty title="Все клиенты спокойны">Ни одного отеля в зоне риска.</Empty>
      ) : (
        <Group>
          {d.at_risk.map((h) => (
            <Row key={h.name}>
              <div className="mb-0.5 flex items-baseline gap-2">
                <b className="text-[15px] font-semibold">{h.name}</b>
                <span className="ml-auto text-[13px] tabular-nums text-muted">
                  {money(h.mrr)} сум/мес
                </span>
              </div>
              <div className="text-[13px] text-muted">
                {h.unanswered > 0 && (
                  <span className="text-clay">без ответа {h.unanswered} · </span>
                )}
                открытых задач {h.open_tasks}
                {h.margin < 0 && <span className="text-clay"> · обслуживание дороже тарифа</span>}
              </div>
              {h.signal && (
                <p className="m-0 mt-1 text-[14px] leading-snug text-ink/75">{h.signal}</p>
              )}
            </Row>
          ))}
        </Group>
      )}

      <SectionHead title="То же самое приходит в Telegram" />
      <div className="mr-8 rounded-[18px] rounded-bl-[6px] bg-bg px-3.5 py-3">
        <div className="mb-1 flex items-center gap-2">
          <b className="text-[14px] font-semibold text-link">Довод</b>
        </div>
        <pre className="m-0 whitespace-pre-wrap font-sans text-[14px] leading-relaxed">
          {d.telegram_text.replace(/\*/g, "")}
        </pre>
        <div className="mt-1 text-right text-[11px] text-muted">09:00</div>
      </div>
      <Note className="mb-4">
        Сводка считается по текущим данным. Сделайте что-нибудь на других вкладках и вернитесь —
        цифры пересчитаются.
      </Note>
    </div>
  );
}
