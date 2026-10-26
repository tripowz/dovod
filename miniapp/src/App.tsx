import { useCallback, useEffect, useState } from "react";
import { ApiError, api, initTelegram, notify, outsideTelegram, tg } from "./lib/tg";
import type { Me } from "./lib/types";
import Inbox from "./screens/Inbox";
import Tasks from "./screens/Tasks";
import Tickets from "./screens/Tickets";
import Clients from "./screens/Clients";
import Report from "./screens/Report";
import StaffMetrics from "./screens/StaffMetrics";

type Tab = "inbox" | "tasks" | "tickets" | "clients" | "report";
// Внутри «Отчёта» две разные вещи: сводка — что горит сейчас, метрики — кто
// как работал за период. Они не конкурируют за место в нижней навигации,
// поэтому переключаются здесь, а не отдельной шестой вкладкой.
type ReportView = "digest" | "staff";

const TABS: { key: Tab; label: string; managerOnly?: boolean }[] = [
  { key: "inbox", label: "Входящие" },
  { key: "tasks", label: "Задачи" },
  { key: "tickets", label: "Заявки" },
  { key: "clients", label: "Клиенты" },
  { key: "report", label: "Отчёт", managerOnly: true },
];

const TITLES: Record<Tab, [string, string]> = {
  inbox: ["Входящие", "Что бот услышал в чатах"],
  tasks: ["Задачи", "Внутренняя работа команды"],
  tickets: ["Заявки", "Обращения отелей и SLA"],
  clients: ["Клиенты", "Здоровье отелей и удержание"],
  report: ["Отчёт", "Сводка и качество работы"],
};

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Руководителю приложение открывается на сводке: он заходит узнать, что
  // горит, а не разбирать входящие.
  const [tab, setTab] = useState<Tab>("inbox");
  const [reportView, setReportView] = useState<ReportView>("digest");
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    initTelegram();
    if (outsideTelegram) {
      setError(
        "Приложение открыто вне Telegram. Подписи нет, а без неё вход невозможен — " +
          "откройте «Довод» из чата с ботом.",
      );
      return;
    }
    api
      .get<Me>("/me")
      .then((u) => {
        setMe(u);
        if (u.is_manager) setTab("report");
      })
      .catch((e: ApiError) => setError(e.message));
  }, []);

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
          tg?.showAlert(err.message);
        } else {
          say(err.message, false);
        }
      }
    },
    [say],
  );

  if (error)
    return (
      <div className="mx-auto max-w-md px-5 py-16 text-center">
        <div className="mb-3 text-[34px]">🎯</div>
        <h1 className="mb-2 text-lg font-bold">Довод</h1>
        <p className="text-[13px] leading-relaxed text-muted">{error}</p>
      </div>
    );

  if (!me)
    return (
      <div className="px-5 py-20 text-center text-[13px] text-muted">Проверяем доступ…</div>
    );

  const visible = TABS.filter((t) => !t.managerOnly || me.is_manager);
  const [title, subtitle] = TITLES[tab];

  return (
    <div className="flex min-h-screen flex-col bg-surface text-ink">
      <header className="flex items-center gap-3 border-b border-black/6 bg-bg px-4 py-2.5">
        <div className="min-w-0">
          <b className="block text-[14.5px] font-bold tracking-tight">{title}</b>
          <span className="block text-[11px] text-muted">{subtitle}</span>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pb-4 pt-3">
        {tab === "inbox" && <Inbox guard={guard} />}
        {tab === "tasks" && <Tasks me={me} guard={guard} />}
        {tab === "tickets" && <Tickets guard={guard} />}
        {tab === "clients" && <Clients guard={guard} />}
        {tab === "report" && (
          <>
            <div className="mb-3 flex gap-1 rounded-xl bg-surface p-1">
              {([
                ["digest", "Сводка"],
                ["staff", "Метрики"],
              ] as [ReportView, string][]).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setReportView(key)}
                  className={`flex-1 rounded-lg px-3 py-1.5 text-[12px] font-semibold ${
                    reportView === key ? "bg-bg text-ink shadow-sm" : "text-muted"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            {reportView === "digest" ? <Report me={me} /> : <StaffMetrics />}
          </>
        )}
      </main>

      {toast && (
        <div className="pointer-events-none fixed inset-x-3 bottom-20 z-40 rounded-xl bg-ink px-3.5 py-2.5 text-[12.5px] leading-snug text-bg shadow-lg">
          {toast}
        </div>
      )}

      <nav
        style={{ gridTemplateColumns: `repeat(${visible.length}, minmax(0, 1fr))` }}
        className="grid shrink-0 border-t border-black/6 bg-bg px-1 pb-[env(safe-area-inset-bottom)] pt-1.5">
        {visible.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-xl px-1 py-1.5 text-[9.5px] font-semibold ${
              tab === t.key ? "text-link" : "text-muted"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
