import { useCallback, useEffect, useState } from "react";
import { ApiError, alertUser, api, haptic, initTelegram, notify, outsideTelegram } from "./lib/tg";
import Login from "./screens/Login";
import { Loading, Segmented } from "./components/ui";
import type { Me } from "./lib/types";
import Inbox from "./screens/Inbox";
import Tasks from "./screens/Tasks";
import Tickets from "./screens/Tickets";
import Clients from "./screens/Clients";
import Report from "./screens/Report";
import StaffMetrics from "./screens/StaffMetrics";
import Team from "./screens/Team";

type Tab = "inbox" | "tasks" | "tickets" | "clients" | "report" | "team";
// Внутри «Отчёта» две разные вещи: сводка — что горит сейчас, метрики — кто
// как работал за период. Они не конкурируют за место в нижней навигации,
// поэтому переключаются здесь, а не отдельной шестой вкладкой.
type ReportView = "digest" | "staff";

/** Иконки таб-бара: контурные, в один штрих, как SF Symbols. */
const ICON: Record<Tab, JSX.Element> = {
  inbox: (
    <path d="M3 13.5 5.6 5.2A1.8 1.8 0 0 1 7.3 4h9.4a1.8 1.8 0 0 1 1.7 1.2L21 13.5M3 13.5V18a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4.5M3 13.5h5l1.5 2.5h5l1.5-2.5h5" />
  ),
  tasks: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="4" />
      <path d="m8.5 12.2 2.4 2.4 4.6-5" />
    </>
  ),
  tickets: (
    <path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.1A8 8 0 1 1 20 12Z" />
  ),
  clients: (
    <>
      <path d="M4 20V7.5L12 4l8 3.5V20" />
      <path d="M9 20v-5h6v5M8.5 10h.01M12 10h.01M15.5 10h.01" />
    </>
  ),
  report: (
    <>
      <path d="M5 20V12M10 20V6M15 20v-9M20 20V4" />
    </>
  ),
  team: (
    <>
      <circle cx="9" cy="8.5" r="3.2" />
      <path d="M3.5 19.5c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8" />
      <path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.5 14.9c1.6.6 2.7 2.2 3 4.6" />
    </>
  ),
};

const TABS: { key: Tab; label: string; managerOnly?: boolean }[] = [
  { key: "inbox", label: "Входящие" },
  { key: "tasks", label: "Задачи" },
  { key: "tickets", label: "Заявки" },
  { key: "clients", label: "Клиенты" },
  { key: "report", label: "Отчёт", managerOnly: true },
  { key: "team", label: "Команда", managerOnly: true },
];

const TITLES: Record<Tab, [string, string]> = {
  inbox: ["Входящие", "Что бот услышал в чатах"],
  tasks: ["Задачи", "Внутренняя работа команды"],
  tickets: ["Заявки", "Обращения отелей и SLA"],
  clients: ["Клиенты", "Здоровье отелей и удержание"],
  report: ["Отчёт", "Сводка и качество работы"],
  team: ["Команда", "Сотрудники и чаты"],
};

/** Колонка контента: на телефоне — во всю ширину, в браузере — по центру. */
const COLUMN = "mx-auto w-full max-w-[760px]";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Руководителю приложение открывается на сводке: он заходит узнать, что
  // горит, а не разбирать входящие.
  const [tab, setTab] = useState<Tab>("inbox");
  const [reportView, setReportView] = useState<ReportView>("digest");
  const [toast, setToast] = useState<string | null>(null);
  // В браузере без сессии — экран входа через Telegram.
  const [needLogin, setNeedLogin] = useState(false);

  const enter = useCallback((u: Me) => {
    setMe(u);
    setNeedLogin(false);
    if (u.is_manager) setTab("report");
  }, []);

  useEffect(() => {
    initTelegram();
    api
      .get<Me>("/me")
      .then(enter)
      .catch((e: ApiError) => {
        if (outsideTelegram && e.status === 401) setNeedLogin(true);
        else setError(e.message);
      });
  }, [enter]);

  const logout = async () => {
    await api.post("/auth/logout").catch(() => undefined);
    setMe(null);
    setNeedLogin(true);
  };

  const say = useCallback((message: string, ok = true) => {
    notify(ok ? "success" : "error");
    setToast(message);
    window.setTimeout(() => setToast(null), 3600);
  }, []);

  /** Единая обработка отказов.
   *  Нарушение правила показывается как решение системы, а не как поломка:
   *  иначе люди привыкают закрывать любые ошибки не читая. */
  const guard = useCallback(
    async (fn: () => Promise<string | void>) => {
      try {
        const message = await fn();
        if (message) say(message);
      } catch (e) {
        const err = e as ApiError;
        if (err.isRule) {
          notify("warning");
          alertUser(err.message);
        } else {
          say(err.message, false);
        }
      }
    },
    [say],
  );

  if (needLogin) return <Login onDone={enter} />;

  if (error)
    return (
      <div className="mx-auto max-w-md px-6 py-20 text-center">
        <div className="mx-auto mb-4 grid h-16 w-16 place-items-center rounded-[18px] bg-accent text-[30px] shadow-[0_6px_20px_rgba(0,122,255,0.3)]">
          🎯
        </div>
        <h1 className="mb-2 text-[22px] font-bold">Довод</h1>
        <p className="text-[15px] leading-relaxed text-muted">{error}</p>
      </div>
    );

  if (!me)
    return (
      <div className="min-h-screen bg-surface pt-24">
        <Loading />
      </div>
    );

  const visible = TABS.filter((t) => !t.managerOnly || me.is_manager);
  const [title, subtitle] = TITLES[tab];

  return (
    <div className="flex min-h-screen flex-col bg-surface text-ink">
      <header className={`${COLUMN} flex items-start gap-3 px-4 pb-2 pt-4`}>
        <div className="min-w-0 flex-1">
          <h1 className="m-0 text-[32px] font-bold leading-tight tracking-[-0.02em]">{title}</h1>
          <span className="block text-[14px] text-muted">{subtitle}</span>
        </div>
        {/* В Telegram выходить некуда — там вход по самому Telegram. */}
        {outsideTelegram && (
          <div className="flex shrink-0 items-center gap-2 pt-3 text-[14px]">
            <span className="hidden text-muted sm:inline">{me.name}</span>
            <button onClick={() => void logout()} className="tap text-link">
              Выйти
            </button>
          </div>
        )}
      </header>

      <main className={`${COLUMN} flex-1 px-4 pb-[calc(84px+env(safe-area-inset-bottom))] pt-2`}>
        {tab === "inbox" && <Inbox guard={guard} />}
        {tab === "tasks" && <Tasks me={me} guard={guard} />}
        {tab === "tickets" && <Tickets guard={guard} />}
        {tab === "clients" && <Clients guard={guard} />}
        {tab === "report" && (
          <>
            <Segmented
              className="mb-4"
              value={reportView}
              onChange={setReportView}
              options={[
                { key: "digest", label: "Сводка" },
                { key: "staff", label: "Метрики" },
              ]}
            />
            {reportView === "digest" ? <Report me={me} /> : <StaffMetrics />}
          </>
        )}
        {tab === "team" && <Team me={me} guard={guard} />}
      </main>

      {toast && (
        <div
          role="status"
          className="pointer-events-none fixed bottom-[calc(76px+env(safe-area-inset-bottom))] left-1/2 z-40 w-[calc(100%-32px)] max-w-[728px] -translate-x-1/2 rounded-[14px] bg-ink/90 px-4 py-3 text-[14px] leading-snug text-bg shadow-[0_8px_30px_rgba(0,0,0,0.18)] backdrop-blur">
          {toast}
        </div>
      )}

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t-[0.5px] border-sep bg-bg/85 px-1 pb-[max(env(safe-area-inset-bottom),6px)] pt-1.5 backdrop-blur-xl backdrop-saturate-150">
        <div
          style={{ gridTemplateColumns: `repeat(${visible.length}, minmax(0, 1fr))` }}
          className="mx-auto grid max-w-[760px]"
        >
        {visible.map((t) => (
          <button
            key={t.key}
            onClick={() => {
              haptic("light");
              setTab(t.key);
              window.scrollTo({ top: 0 });
            }}
            className={`flex flex-col items-center gap-0.5 py-0.5 text-[10px] font-medium ${
              tab === t.key ? "text-accent" : "text-muted"
            }`}
          >
            <svg
              width="26"
              height="26"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={tab === t.key ? 2.1 : 1.7}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              {ICON[t.key]}
            </svg>
            {t.label}
          </button>
        ))}
        </div>
      </nav>
    </div>
  );
}
