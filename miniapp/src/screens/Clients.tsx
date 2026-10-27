import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { HotelRow } from "../lib/types";
import {
  Back,
  Button,
  Card,
  Empty,
  Group,
  Loading,
  Note,
  Row,
  SectionHead,
  Tag,
  money,
  plural,
} from "../components/ui";

const RISK: Record<string, { label: string; cls: string }> = {
  high: { label: "риск высокий", cls: "bg-clay/12 text-clay" },
  medium: { label: "под вопросом", cls: "bg-ochre/15 text-ochre" },
  low: { label: "всё ровно", cls: "bg-moss/12 text-moss" },
};

/** «сегодня», «вчера», «5 дн. назад» — по дате последнего сообщения. */
function lastSeen(at: string | null): string {
  if (!at) return "—";
  const days = Math.floor((Date.now() - new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(at) ? at : at + "Z").getTime()) / 86_400_000);
  if (days <= 0) return "сегодня";
  if (days === 1) return "вчера";
  return `${days} дн. назад`;
}

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

  if (!rows) return <Loading />;
  if (rows.length === 0)
    return <Empty title="Клиентов нет">Справочник отелей пока пуст.</Empty>;

  if (open) {
    const r = RISK[open.risk];
    return (
      <div className="flex flex-col">
        <Back onClick={() => setOpen(null)}>Клиенты</Back>

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <Tag className={r.cls}>{r.label}</Tag>
          <Tag className="bg-fill text-muted">
            {open.rooms} {plural(open.rooms, ["номер", "номера", "номеров"])}
          </Tag>
        </div>

        <h2 className="m-0 mt-2 text-[22px] font-bold tracking-[-0.01em]">{open.name}</h2>
        {(open.city || open.tariff) && (
          <div className="mt-0.5 text-[14px] text-muted">
            {[open.city, open.tariff].filter(Boolean).join(" · ")}
          </div>
        )}

        {open.chats.length > 0 && (
          <>
            <SectionHead title="Переписка за 30 дней" />
            <div className="grid grid-cols-3 gap-2.5">
              <Card>
                <div className="text-[24px] font-bold leading-none tabular-nums">{open.requests_30d}</div>
                <div className="mt-1 text-[12px] leading-snug text-muted">обращений</div>
              </Card>
              <Card>
                <div className="text-[24px] font-bold leading-none tabular-nums">{open.messages_30d}</div>
                <div className="mt-1 text-[12px] leading-snug text-muted">сообщений</div>
              </Card>
              <Card>
                <div className="whitespace-nowrap text-[15px] font-bold leading-[24px]">{lastSeen(open.last_message_at)}</div>
                <div className="mt-1 text-[12px] leading-snug text-muted">последнее</div>
              </Card>
            </div>
          </>
        )}

        {/* Экономика — это то, что превращает «клиент в риске» в решение.
            «Registon в риске» и «Registon в риске, 1 800 000 в месяц» —
            разные разговоры. */}
        {open.mrr === 0 && open.cost === 0 ? (
          <Note>
            Тариф не указан — экономику не посчитать. Заполнить:{" "}
            <span className="font-mono">
              python -m app.hotels set "{open.name}" mrr=… tariff=…
            </span>
          </Note>
        ) : (
        <div>
          <SectionHead title="Сколько платит, сколько стоит" />
          <div className="grid grid-cols-2 gap-2.5">
            <Card>
              <div className="text-[20px] font-bold leading-tight tabular-nums">{money(open.mrr)}</div>
              <div className="mt-0.5 text-[12.5px] leading-snug text-muted">сум в месяц по тарифу</div>
            </Card>
            <Card>
              <div className="text-[20px] font-bold leading-tight tabular-nums">{money(open.cost)}</div>
              <div className="mt-0.5 text-[12.5px] leading-snug text-muted">
                сум обслуживания за месяц
              </div>
            </Card>
          </div>
          <div
            className={`mt-2.5 rounded-[14px] px-3.5 py-3 text-[14px] font-medium leading-snug ${
              open.margin > 0 ? "bg-moss/12 text-moss" : "bg-clay/10 text-clay"
            }`}
          >
            {open.verdict}
          </div>
        </div>
        )}

        <div className="mt-2.5 grid grid-cols-2 gap-2.5">
          <Card>
            <div className={`text-[28px] font-bold leading-none tabular-nums ${open.open_tasks > 1 ? "text-clay" : ""}`}>
              {open.open_tasks}
            </div>
            <div className="mt-1 text-[12.5px] leading-snug text-muted">открытых задач сейчас</div>
          </Card>
          <Card>
            <div
              className={`text-[28px] font-bold leading-none tabular-nums ${open.unanswered ? "text-clay" : "text-moss"}`}
            >
              {open.unanswered}
            </div>
            <div className="mt-1 text-[12.5px] leading-snug text-muted">заявок без ответа клиенту</div>
          </Card>
        </div>

        {open.signals.length > 0 && (
          <div>
            <SectionHead title="Что видно по данным" />
            <Group>
              {open.signals.map((s, i) => (
                <Row key={i}>
                  <div className="flex gap-2.5 text-[15px] leading-snug">
                    <i className="mt-[7px] block h-1.5 w-1.5 shrink-0 rounded-full bg-clay" />
                    {s}
                  </div>
                </Row>
              ))}
            </Group>
          </div>
        )}

        {open.chats.length > 0 && (
          <div>
            <SectionHead title="Чаты" right={open.chats.length} />
            <Group>
              {open.chats.map((c) => (
                <Row key={c}>
                  <span className="text-[15px]">{c}</span>
                </Row>
              ))}
            </Group>
          </div>
        )}

        {open.offers.length > 0 && (
          <div className="pb-4">
            <SectionHead title="Что предложить" />
            <div className="flex flex-col gap-2.5">
              {open.offers.map((o, i) => (
                <Card key={i}>
                  <h4 className="m-0 text-[16px] font-semibold">{o.h}</h4>
                  <p className="m-0 mt-1 text-[14px] leading-snug text-ink/75">{o.p}</p>
                  <div className="mt-2.5 flex items-center">
                    <span className="text-[13px] text-muted">цена: {o.c}</span>
                    <Button
                      variant="ghost"
                      className="ml-auto"
                      onClick={() =>
                        void guard(async () => {
                          haptic("medium");
                          const r = await api.post<{ message: string }>(
                            `/hotels/${open.id}/offers/${i}/task`,
                          );
                          return r.message;
                        })
                      }
                    >
                      В задачи
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
            <Note>
              Предложения собраны из данных отеля: обращения, время решения, оплата, активность.
              Решение всё равно за человеком.
            </Note>
          </div>
        )}
      </div>
    );
  }

  const risky = rows.filter((r) => r.risk !== "low").length;
  return (
    <div>
      <Note className="!mt-0 mb-3">
        {rows.length} {plural(rows.length, ["отель", "отеля", "отелей"])} на обслуживании ·{" "}
        <b className="font-semibold text-ink/75">{risky}</b> требуют внимания
      </Note>
      <Group>
        {rows.map((h) => {
          const r = RISK[h.risk];
          return (
            <Row
              key={h.id}
              onClick={() => {
                haptic();
                setOpen(h);
              }}
            >
              <div className="flex items-baseline gap-2">
                <p className="m-0 min-w-0 flex-1 truncate text-[16px] font-semibold">{h.name}</p>
                {h.mrr > 0 && (
                  <span className="shrink-0 text-[13px] tabular-nums text-muted">
                    {money(h.mrr)} сум/мес
                  </span>
                )}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-muted">
                <Tag className={r.cls}>{r.label}</Tag>
                <span className="truncate">
                  {[h.city, h.tariff].filter(Boolean).join(" · ") ||
                    (h.chats.length ? `${h.requests_30d} обращ. за 30 дн.` : "чаты не привязаны")}
                </span>
              </div>
              {h.signals[0] && (
                <p className="m-0 mt-1.5 text-[14px] leading-snug text-ink/75">{h.signals[0]}</p>
              )}
              <div className="mt-1.5 flex gap-3 text-[13px] text-muted">
                <span>
                  открыто <b className="font-semibold text-ink/75">{h.open_tasks}</b>
                </span>
                <span className={h.unanswered ? "text-clay" : ""}>
                  без ответа <b className="font-semibold">{h.unanswered}</b>
                </span>
              </div>
            </Row>
          );
        })}
      </Group>
    </div>
  );
}
