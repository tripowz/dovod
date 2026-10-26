import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../lib/tg";
import type { StaffMetric, StaffReportOut } from "../lib/types";
import { Card, Empty, Loading, Note, SectionHead, Segmented, Tag, plural } from "../components/ui";

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

/**
 * Стрелка динамики к прошлому периоду.
 *
 * Куда «хорошо» — зависит от показателя, и это знает вызывающий: для закрытых
 * задач рост хорош, для возвратов и времени ответа хорош спад. Хорошее —
 * зелёным (moss), тревожное — красным (clay).
 * Ничего не рисуем при нуле или когда сравнивать не с чем.
 */
function Delta({ value, goodWhenDown = false }: { value: number | null; goodWhenDown?: boolean }) {
  if (value === null || value === 0) return null;
  const down = value < 0;
  const good = goodWhenDown ? down : !down;
  return (
    <span className={`ml-1 text-[11px] font-semibold ${good ? "text-moss" : "text-clay"}`}>
      {down ? "↓" : "↑"}
      {Math.abs(value)}
    </span>
  );
}

function Stat({
  label,
  value,
  tone,
  delta,
}: {
  label: string;
  value: string;
  tone?: "bad";
  delta?: ReactNode;
}) {
  return (
    <div>
      <div className="flex items-baseline">
        <span
          className={`text-[20px] font-bold leading-none tabular-nums ${tone === "bad" ? "text-clay" : "text-ink"}`}
        >
          {value}
        </span>
        {delta}
      </div>
      <div className="mt-1 text-[12px] text-muted">{label}</div>
    </div>
  );
}

function PersonCard({ m }: { m: StaffMetric }) {
  // Возвраты и просрочки — повод для разговора, поэтому красные, когда они есть.
  const t = m.trend;
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
    <Card>
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent/15 text-[14px] font-semibold text-accent">
          {m.name.slice(0, 1)}
        </span>
        <b className="text-[16px] font-semibold">{m.name}</b>
        {m.reworks > 0 && (
          <Tag className="bg-clay/12 text-clay">брак {Math.round(m.rework_rate * 100)}%</Tag>
        )}
        <span className="ml-auto text-right text-[12px] text-muted">
          открыто {m.open_now}
          {m.review_now > 0 && ` · на проверке ${m.review_now}`}
        </span>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <Stat label="закрыто" value={String(m.done)} delta={<Delta value={t.done} />} />
        <Stat
          label="заявок"
          value={String(m.resolved_tickets)}
          delta={<Delta value={t.resolved_tickets} />}
        />
        <Stat
          label="ответ"
          value={reply(m.avg_first_reply_min)}
          delta={<Delta value={t.avg_first_reply_min} goodWhenDown />}
        />
        <Stat
          label="возвраты"
          value={String(m.reworks)}
          tone={m.reworks > 0 ? "bad" : undefined}
          delta={<Delta value={t.reworks} goodWhenDown />}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t-[0.5px] border-sep pt-2.5 text-[12.5px] text-muted">
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
      <Segmented
        value={days}
        onChange={setDays}
        options={PERIODS.map((p) => ({ key: p.days, label: p.label }))}
      />

      <SectionHead
        title="Кто как работал"
        right={data ? `за ${data.days} ${plural(data.days, ["день", "дня", "дней"])}` : ""}
      />

      {loading && !data ? (
        <Loading />
      ) : !data || data.staff.length === 0 ? (
        <Empty title="Нет данных">
          За выбранный период никто не вёл задач и не отвечал на заявки.
        </Empty>
      ) : (
        <div className="flex flex-col gap-2.5">
          {data.staff.map((m) => (
            <PersonCard key={m.staff_id} m={m} />
          ))}
        </div>
      )}

      <Note className="mb-4 mt-3">
        Стрелка — сравнение с предыдущим таким же периодом: зелёная — движение в
        хорошую сторону, красная — в плохую. Возврат на доработку — это задача,
        которую автор вернул с проверки: самый честный признак того, что работа
        была сдана недоделанной. Считается из истории задач, а не со слов
        исполнителя.
      </Note>
    </div>
  );
}
