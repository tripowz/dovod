import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { HotelRow } from "../lib/types";
import { Button, Card, Empty, SectionHead, money, plural } from "../components/ui";

const RISK: Record<string, { label: string; cls: string }> = {
  high: { label: "риск высокий", cls: "bg-clay/12 text-clay" },
  medium: { label: "под вопросом", cls: "bg-ochre/12 text-ochre" },
  low: { label: "всё ровно", cls: "bg-moss/12 text-moss" },
};

export default function Clients({
  guard,
}: {
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [rows, setRows] = useState<HotelRow[] | null>(null);
  const [open, setOpen] = useState<HotelRow | null>(null);

  useEffect(() => {
    void api.get<HotelRow[]>("/hotels").then(setRows);
  }, []);

  if (!rows) return <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>;
  if (rows.length === 0)
    return <Empty title="Клиентов нет">Справочник отелей пока пуст.</Empty>;

  if (open) {
    const r = RISK[open.risk];
    return (
      <div className="flex flex-col gap-3">
        <button onClick={() => setOpen(null)} className="self-start text-[13px] font-semibold text-link">
          ‹ К списку
        </button>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`rounded px-2 py-0.5 text-[10.5px] font-bold uppercase ${r.cls}`}>
            {r.label}
          </span>
          <span className="rounded bg-surface px-2 py-0.5 text-[11px] text-muted">
            {open.rooms} {plural(open.rooms, ["номер", "номера", "номеров"])}
          </span>
        </div>

        <h2 className="m-0 text-[17px] font-bold">{open.name}</h2>
        <div className="text-[11.5px] text-muted">
          {open.city} · {open.tariff}
        </div>

        {/* Экономика — это то, что превращает «клиент в риске» в решение.
            «Registon в риске» и «Registon в риске, 1 800 000 в месяц» —
            разные разговоры. */}
        <div>
          <SectionHead title="Сколько платит, сколько стоит" />
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-xl bg-surface px-3 py-2.5">
              <div className="font-mono text-[17px] font-bold leading-tight">
                {money(open.mrr)}
              </div>
              <div className="mt-0.5 text-[10.5px] leading-snug text-muted">
                сум в месяц по тарифу
              </div>
            </div>
            <div className="rounded-xl bg-surface px-3 py-2.5">
              <div className="font-mono text-[17px] font-bold leading-tight">
                {money(open.cost)}
              </div>
              <div className="mt-0.5 text-[10.5px] leading-snug text-muted">
                сум обслуживания за месяц
              </div>
            </div>
          </div>
          <div
            className={`mt-2 rounded-xl px-3 py-2.5 text-[12.5px] leading-snug ${
              open.margin > 0 ? "bg-moss/10 text-moss" : "bg-clay/10 text-clay"
            }`}
          >
            {open.verdict}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-xl bg-surface px-3 py-2.5">
            <div
              className={`font-mono text-[20px] font-bold ${open.open_tasks > 1 ? "text-clay" : ""}`}
            >
              {open.open_tasks}
            </div>
            <div className="text-[10.5px] leading-snug text-muted">открытых задач сейчас</div>
          </div>
          <div className="rounded-xl bg-surface px-3 py-2.5">
            <div className={`font-mono text-[20px] font-bold ${open.unanswered ? "text-clay" : "text-moss"}`}>
              {open.unanswered}
            </div>
            <div className="text-[10.5px] leading-snug text-muted">заявок без ответа клиенту</div>
          </div>
        </div>

        {open.signals.length > 0 && (
          <div>
            <SectionHead title="Что видно по данным" />
            <div className="flex flex-col gap-2">
              {open.signals.map((s, i) => (
                <div key={i} className="flex gap-2 text-[12.5px] leading-snug text-ink/75">
                  <i className="mt-1.5 block h-1.5 w-1.5 shrink-0 rounded-full bg-clay" />
                  {s}
                </div>
              ))}
            </div>
          </div>
        )}

        {open.offers.length > 0 && (
          <div className="pb-4">
            <SectionHead title="Что предложить" />
            <div className="flex flex-col gap-2">
              {open.offers.map((o, i) => (
                <div key={i} className="rounded-xl border border-black/6 bg-surface px-3 py-2.5">
                  <h4 className="m-0 text-[13px] font-bold">{o.h}</h4>
                  <p className="m-0 mt-1 text-[12px] leading-snug text-ink/70">{o.p}</p>
                  <div className="mt-2 flex items-center">
                    <span className="font-mono text-[11px] text-muted">цена: {o.c}</span>
                    <Button
                      variant="ghost"
                      className="ml-auto"
                      onClick={() =>
                        void guard(async () => {
                          haptic("medium");
                          return "Задача из предложения создаётся на следующем шаге";
                        })
                      }
                    >
                      В задачи
                    </Button>
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[11px] leading-snug text-muted">
              Предложения собраны из данных отеля: обращения, время решения, оплата, активность.
              Решение всё равно за человеком.
            </p>
          </div>
        )}
      </div>
    );
  }

  const risky = rows.filter((r) => r.risk !== "low").length;
  return (
    <div>
      <div className="px-1 pb-2 text-[11.5px] leading-snug text-muted">
        {rows.length} {plural(rows.length, ["отель", "отеля", "отелей"])} на обслуживании ·{" "}
        <b className="font-mono text-ink/70">{risky}</b> требуют внимания
      </div>
      <div className="flex flex-col gap-2">
        {rows.map((h) => {
          const r = RISK[h.risk];
          return (
            <Card key={h.id} onClick={() => { haptic(); setOpen(h); }}>
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className={`rounded px-2 py-0.5 text-[10.5px] font-bold uppercase ${r.cls}`}>
                  {r.label}
                </span>
                <span className="ml-auto font-mono text-[11px] text-muted">
                  {money(h.mrr)} сум/мес
                </span>
              </div>
              <p className="m-0 text-[14px] font-semibold">{h.name}</p>
              <div className="mt-1 text-[11.5px] text-muted">
                {h.city} · {h.tariff}
              </div>
              {h.signals[0] && (
                <p className="m-0 mt-2 text-[12.5px] leading-snug text-ink/70">{h.signals[0]}</p>
              )}
              <div className="mt-2 flex gap-3 text-[11px] text-muted">
                <span>
                  открыто <b className="font-mono text-ink/70">{h.open_tasks}</b>
                </span>
                <span className={h.unanswered ? "text-clay" : ""}>
                  без ответа <b className="font-mono">{h.unanswered}</b>
                </span>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
