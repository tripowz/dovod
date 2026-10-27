import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/tg";
import type { ChatRow, ChatsFull } from "../lib/types";
import {
  Button,
  Card,
  Choice,
  Empty,
  Group,
  Loading,
  Note,
  Row,
  Segmented,
  Switch,
  Tag,
  plural,
} from "../components/ui";

/**
 * Чаты, которые видит Telethon.
 *
 * Здесь руководитель решает, что «Довод» читает: включает чаты, отмечает
 * поддержку (из неё создаются заявки с SLA) и привязывает чаты к отелям — из
 * этой переписки считаются карточки клиентов.
 */

const KIND: Record<ChatRow["kind"], string> = {
  support: "поддержка",
  team: "команда",
  dev: "разработка",
  sales: "продажи",
  private: "личная",
  other: "другое",
};
const GROUP_KINDS: ChatRow["kind"][] = ["support", "team", "dev", "sales", "other"];

type Filter = "on" | "groups" | "private" | "all";
const PAGE = 60;

function ago(at: string | null): string {
  if (!at) return "сообщений нет";
  const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(at) ? at : at + "Z").getTime();
  const days = Math.floor((Date.now() - t) / 86_400_000);
  if (days <= 0) return "сегодня";
  if (days === 1) return "вчера";
  return `${days} дн. назад`;
}

export default function Chats({
  guard,
}: {
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [data, setData] = useState<ChatsFull | null>(null);
  const [filter, setFilter] = useState<Filter>("on");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [days, setDays] = useState(30);

  const load = () => api.get<ChatsFull>("/chats/full").then(setData);
  useEffect(() => {
    void load();
  }, []);
  // Пока идёт обновление или загрузка истории — подтягиваем состояние.
  const busy = !!data && (data.syncing || !!data.history);
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(t);
  }, [busy]);

  const list = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.chats.filter((c) => {
      if (filter === "on" && !c.enabled) return false;
      if (filter === "groups" && c.kind === "private") return false;
      if (filter === "private" && c.kind !== "private") return false;
      if (q && !c.title.toLowerCase().includes(q) && !c.hotel.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, filter, query]);

  if (!data) return <Loading />;

  const enabled = data.chats.filter((c) => c.enabled).length;
  const offGroups = data.chats.filter((c) => !c.enabled && c.kind !== "private").length;
  const offPrivate = data.chats.filter((c) => !c.enabled && c.kind === "private").length;

  const patch = (c: ChatRow, body: Record<string, unknown>, msg: string) =>
    guard(async () => {
      await api.patch(`/chats/${c.id}`, body);
      await load();
      return msg;
    });

  const toggle = (c: ChatRow, on: boolean) => {
    if (on && c.kind === "private" && !c.consent_note) {
      const note = window.prompt(
        `«${c.title}» — личная переписка. Основание читать её (чья переписка, чьё согласие):`,
        "служебный аккаунт, рабочая переписка с отелем",
      );
      if (!note?.trim()) return;
      return void patch(c, { enabled: true, consent_note: note.trim() }, `«${c.title}» включён`);
    }
    void patch(c, { enabled: on }, on ? `«${c.title}» включён` : `«${c.title}» выключен`);
  };

  const enableAll = (privateToo: boolean) =>
    guard(async () => {
      let consent = "";
      if (privateToo) {
        consent =
          window.prompt(
            "Основание читать личные переписки (сохранится в каждом чате):",
            "служебный аккаунт Smartbooking, рабочая переписка с отелями",
          ) ?? "";
        if (!consent.trim()) return;
      }
      const r = await api.post<{ message: string }>("/chats/enable-all", {
        private: privateToo,
        consent_note: consent.trim(),
      });
      await load();
      return r.message;
    });

  const sync = () =>
    guard(async () => {
      const r = await api.post<{ message: string }>("/sync");
      await load();
      return r.message;
    });

  const history = () =>
    guard(async () => {
      const r = await api.post<{ message: string }>("/chats/history", { days });
      await load();
      return r.message;
    });

  return (
    <div>
      <Card>
        <div className="flex items-baseline gap-2">
          <b className="text-[17px] font-semibold">
            Читается {enabled} из {data.chats.length}
          </b>
        </div>
        {data.live ? (
          <>
            <p className="m-0 mt-1 text-[13px] leading-snug text-muted">
              Новые сообщения забираются раз в 15 минут.{" "}
              {data.syncing ? "Сейчас обновляется…" : ""}
              {data.history ? ` История ${data.history}` : data.history_done ? ` ${data.history_done}.` : ""}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button className="!px-3.5 !py-2 !text-[14px]" disabled={data.syncing} onClick={() => void sync()}>
                {data.syncing ? "Обновляется…" : "Обновить сейчас"}
              </Button>
              <Button
                variant="ghost"
                className="!px-3.5 !py-2 !text-[14px]"
                disabled={!!data.history}
                onClick={() => void history()}
              >
                Загрузить историю
              </Button>
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                aria-label="За сколько дней"
                className="rounded-[10px] bg-fill px-2.5 py-2 text-[14px] text-ink outline-none"
              >
                <option value={7}>за 7 дней</option>
                <option value={30}>за 30 дней</option>
                <option value={90}>за 90 дней</option>
              </select>
            </div>
          </>
        ) : (
          <p className="m-0 mt-1 text-[13px] leading-snug text-muted">
            Чтение чатов работает отдельным процессом. Чтобы обновлять и загружать историю
            отсюда, запускайте всё одной командой: <span className="font-mono">python -m app.run</span>.
          </p>
        )}
        {(offGroups > 0 || offPrivate > 0) && (
          <div className="mt-3 flex flex-wrap gap-2 border-t-[0.5px] border-sep pt-3">
            {offGroups > 0 && (
              <Button variant="ghost" className="!px-3.5 !py-2 !text-[14px]" onClick={() => void enableAll(false)}>
                Включить все группы ({offGroups})
              </Button>
            )}
            {offPrivate > 0 && (
              <Button variant="ghost" className="!px-3.5 !py-2 !text-[14px]" onClick={() => void enableAll(true)}>
                Включить и личные ({offPrivate})
              </Button>
            )}
          </div>
        )}
      </Card>

      <input
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setLimit(PAGE);
        }}
        placeholder="Поиск по названию или отелю"
        aria-label="Поиск чатов"
        className="field mt-4 !bg-bg"
      />
      <Segmented
        className="mt-3"
        value={filter}
        onChange={(f) => {
          setFilter(f);
          setLimit(PAGE);
        }}
        options={[
          { key: "on", label: "Читаются" },
          { key: "groups", label: "Группы" },
          { key: "private", label: "Личные" },
          { key: "all", label: "Все" },
        ]}
      />

      <div className="mt-3">
        {list.length === 0 ? (
          <Empty title={filter === "on" ? "Ни один чат не читается" : "Ничего не найдено"}>
            {filter === "on" ? "Включите нужные на вкладке «Все» или кнопками выше." : "Попробуйте другой запрос."}
          </Empty>
        ) : (
          <Group>
            {list.slice(0, limit).map((c) => (
              <ChatItem
                key={c.id}
                c={c}
                hotels={data.hotels}
                open={open === c.id}
                onOpen={() => setOpen(open === c.id ? null : c.id)}
                onToggle={(on) => toggle(c, on)}
                onPatch={(body, msg) => void patch(c, body, msg)}
              />
            ))}
          </Group>
        )}
        {list.length > limit && (
          <button onClick={() => setLimit(limit + PAGE)} className="tap mx-auto mt-3 block text-[15px] text-link">
            Показать ещё ({list.length - limit})
          </button>
        )}
      </div>
      <Note className="mb-4">
        Включённый чат читается с момента включения. Старые сообщения — кнопкой «Загрузить
        историю». Заявки с таймером SLA создаются из чатов типа «поддержка».
      </Note>
    </div>
  );
}

function ChatItem({
  c,
  hotels,
  open,
  onOpen,
  onToggle,
  onPatch,
}: {
  c: ChatRow;
  hotels: string[];
  open: boolean;
  onOpen: () => void;
  onToggle: (on: boolean) => void;
  onPatch: (body: Record<string, unknown>, msg: string) => void;
}) {
  const [hotel, setHotel] = useState(c.hotel);
  useEffect(() => setHotel(c.hotel), [c.hotel]);

  return (
    <Row
      right={<Switch on={c.enabled} onChange={onToggle} label={`Читать «${c.title}»`} />}
    >
      <button onClick={onOpen} className="tap block w-full text-left">
        <p className="m-0 truncate text-[16px] font-medium">{c.title}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
          <Tag className={c.kind === "support" ? "bg-link/12 text-link" : c.kind === "private" ? "bg-ochre/15 text-ochre" : "bg-fill text-muted"}>
            {KIND[c.kind]}
          </Tag>
          {c.hotel && <span className="text-ink/75">{c.hotel}</span>}
          <span>
            {c.messages > 0
              ? `${c.messages} ${plural(c.messages, ["сообщение", "сообщения", "сообщений"])} · ${ago(c.last_message_at)}`
              : "сообщений нет"}
          </span>
        </div>
      </button>

      {open && (
        <div className="mt-3 flex flex-col gap-3 border-t-[0.5px] border-sep pt-3">
          {c.kind !== "private" && (
            <div>
              <span className="mb-1.5 block text-[13px] text-muted">Тип</span>
              <div className="flex flex-wrap gap-2">
                {GROUP_KINDS.map((k) => (
                  <Choice
                    key={k}
                    on={c.kind === k}
                    onClick={() => onPatch({ kind: k }, `«${c.title}»: ${KIND[k]}`)}
                    className="!bg-fill data-[on=true]:!bg-accent"
                  >
                    {KIND[k]}
                  </Choice>
                ))}
              </div>
            </div>
          )}
          <div>
            <span className="mb-1.5 block text-[13px] text-muted">Отель</span>
            <div className="flex gap-2">
              <input
                value={hotel}
                onChange={(e) => setHotel(e.target.value)}
                list="dovod-hotels"
                placeholder="не привязан"
                aria-label="Отель"
                className="field"
              />
              <Button
                variant="ghost"
                className="shrink-0 !px-3.5 !py-2 !text-[14px]"
                disabled={hotel.trim() === c.hotel}
                onClick={() =>
                  onPatch(
                    { hotel: hotel.trim() },
                    hotel.trim() ? `«${c.title}» → ${hotel.trim()}` : `«${c.title}» отвязан от отеля`,
                  )
                }
              >
                Сохранить
              </Button>
            </div>
            <datalist id="dovod-hotels">
              {hotels.map((h) => (
                <option key={h} value={h} />
              ))}
            </datalist>
          </div>
          {c.consent_note && (
            <p className="m-0 text-[13px] leading-snug text-muted">Основание: {c.consent_note}</p>
          )}
        </div>
      )}
    </Row>
  );
}
