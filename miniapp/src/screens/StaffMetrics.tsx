import { useEffect, useState } from "react";
import { api } from "../lib/tg";
import type { StaffMetric, StaffReportOut } from "../lib/types";
import { Card, Empty, SectionHead, plural } from "../components/ui";

/**
 * Метрики сотрудников за период.
 *
 * Отвечает на вопрос, которого нет в утренней сводке: не «что горит сейчас»,
 * а «кто как работал». По сводке с человека не спросишь — она про мгновенное
 * состояние; по накопленным цифрам можно.
 *
 * Порядок строк задаёт бэкенд, и он намеренно не «кто больше закрыл»: наверх
 * поднимается тот, у кого возвраты на доработку и просрочки. Это и есть список
 * тех, с кем стоит поговорить, — поэтому возвраты и просрочки выделены цветом,
 * а не спрятаны в ряду одинаковых цифр.
 */

const PERIODS: { days: number; label: string }[] = [
  { days: 7, label: "Неделя" },
  { days: 30, label: "Месяц" },
];

/** Минуты в человеческий вид: «12 мин», «1 ч 20 мин», «—» если не отвечали. */
function reply(min: number | null): string {
  if (min === null) return "—";
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} ч ${m} мин` : `${h} ч`;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "bad" }) {
  return (
    <div>
      <div className={`font-mono text-[17px] font-bold leading-none ${tone === "bad" ? "text-clay" : "text-ink"}`}>
        {value}
      </div>
      <div className="mt-1 text-[10px] uppercase tracking-wide text-muted">{label}</div>
    </div>
  );
}

function PersonCard({ m }: { m: StaffMetric }) {
  // Возвраты и просрочки — повод для разговора, поэтому красные, когда они есть.
  const gap = m.estimate_gap_hours;
  const gapText =
    m.spent_hours === 0 && m.estimate_hours === 0
      ? "—"
      : gap > 0
        ? `+${gap} ч`
        : gap < 0
          ? `${gap} ч`
          : "в оценку";

  return (
    <Card className="!p-3">
      <div className="mb-2.5 flex items-baseline gap-2">
        <b className="text-[14px] font-semibold">{m.name}</b>
        {m.reworks > 0 && (
          <span className="rounded bg-clay/12 px-1.5 py-0.5 text-[10px] font-bold text-clay">
            брак {Math.round(m.rework_rate * 100)}%
          </span>
        )}
        <span className="ml-auto text-[10.5px] text-muted">
          открыто {m.open_now}
          {m.review_now > 0 && ` · на проверке ${m.review_now}`}
        </span>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <Stat label="закрыто" value={String(m.done)} />
        <Stat label="заявок" value={String(m.resolved_tickets)} />
        <Stat label="ответ" value={reply(m.avg_first_reply_min)} />
        <Stat
          label="возвраты"
          value={String(m.reworks)}
          tone={m.reworks > 0 ? "bad" : undefined}
        />
      </div>

      <div className="mt-2.5 flex items-center gap-3 border-t border-black/6 pt-2 text-[11px] text-muted">
        {m.overdue_now > 0 ? (
          <span className="font-semibold text-clay">
            просрочено {m.overdue_now}{" "}
            {plural(m.overdue_now, ["задача", "задачи", "задач"])}
          </span>
        ) : (
          <span>просрочек нет</span>
        )}
        <span className="ml-auto">
          часы: факт {m.spent_hours} / оценка {m.estimate_hours}{" "}
          <span className={gap > 0 ? "text-ochre" : undefined}>({gapText})</span>
        </span>
      </div>
    </Card>
  );
}

export default function StaffMetrics() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<StaffReportOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    void api
      .get<StaffReportOut>(`/report/staff?days=${days}`)
      .then(setData)
      .finally(() => setLoading(false));
  }, [days]);

  return (
    <div>
      {/* Переключатель периода. Неделя — для разговора один на один, месяц —
          чтобы единичная плохая неделя не выглядела как тенденция. */}
      <div className="mb-3 flex gap-1 rounded-xl bg-surface p-1">
        {PERIODS.map((p) => (
          <button
            key={p.days}
            onClick={() => setDays(p.days)}
            className={`flex-1 rounded-lg px-3 py-1.5 text-[12px] font-semibold ${
              days === p.days ? "bg-bg text-ink shadow-sm" : "text-muted"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <SectionHead
        title="Кто как работал"
        right={data ? `за ${data.days} ${plural(data.days, ["день", "дня", "дней"])}` : ""}
      />

      {loading && !data ? (
        <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>
      ) : !data || data.staff.length === 0 ? (
        <Empty title="Нет данных">
          За выбранный период никто не вёл задач и не отвечал на заявки.
        </Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {data.staff.map((m) => (
            <PersonCard key={m.staff_id} m={m} />
          ))}
        </div>
      )}

      <p className="mb-4 mt-3 px-1 text-[11px] leading-snug text-muted">
        Возврат на доработку — это задача, которую автор вернул с проверки:
        самый честный признак того, что работа была сдана недоделанной. Считается
        из истории задач, а не со слов исполнителя.
      </p>
    </div>
  );
}
