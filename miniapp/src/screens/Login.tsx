import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../lib/tg";
import type { Me } from "../lib/types";
import { Button } from "../components/ui";

/**
 * Вход на сайт через Telegram.
 *
 * Шаг 1 — кнопка открывает бота по ссылке с одноразовым ключом.
 * Шаг 2 — бот присылает код, человек вводит его здесь.
 * Код, а не «подтвердите в боте»: так чужая ссылка на вход, присланная
 * сотруднику, никому не открывает доступ под его именем.
 */
export default function Login({ onDone }: { onDone: (me: Me) => void }) {
  const [start, setStart] = useState<{ nonce: string; url: string; bot: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const begin = async () => {
    setError(null);
    setBusy(true);
    // Окно открывается сразу, по нажатию: открытое после ответа сервера
    // браузер сочтёт всплывающим и заблокирует.
    const tab = window.open("", "_blank");
    try {
      const s = await api.post<{ nonce: string; url: string; bot: string }>("/auth/start");
      setStart(s);
      setCode("");
      if (tab) {
        tab.opener = null;
        tab.location.href = s.url;
      }
    } catch (e) {
      tab?.close();
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (value: string) => {
    if (!start) return;
    setBusy(true);
    setError(null);
    try {
      await api.post<Me>("/auth/verify", { nonce: start.nonce, code: value });
      onDone(await api.get<Me>("/me"));
    } catch (e) {
      const err = e as ApiError;
      setError(err.message);
      // Ссылка устарела или попытки кончились — начинать заново.
      if (err.status === 410 || err.status === 429) setStart(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (start) input.current?.focus();
  }, [start]);

  const digits = code.replace(/\D/g, "").slice(0, 6);

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4 py-10 text-ink">
      <div className="w-full max-w-[380px]">
        <div className="mx-auto mb-5 grid h-[72px] w-[72px] place-items-center rounded-[20px] bg-accent text-[34px] shadow-[0_8px_24px_rgba(0,122,255,0.28)]">
          🎯
        </div>
        <h1 className="m-0 text-center text-[28px] font-bold tracking-[-0.02em]">Довод</h1>
        <p className="mx-auto mb-7 mt-1 max-w-[300px] text-center text-[15px] leading-snug text-muted">
          Задачи, заявки отелей и клиенты команды SmartBooking
        </p>

        <div className="rounded-[14px] bg-bg p-5">
          {!start ? (
            <>
              <p className="m-0 mb-4 text-[15px] leading-snug">
                Вход через Telegram: бот пришлёт код, вы введёте его здесь.
              </p>
              <Button className="w-full !py-3 !text-[17px]" disabled={busy} onClick={() => void begin()}>
                {busy ? "Открываю…" : "Войти через Telegram"}
              </Button>
            </>
          ) : (
            <>
              <ol className="m-0 mb-4 flex list-none flex-col gap-2.5 p-0 text-[15px] leading-snug">
                <li className="flex gap-2.5">
                  <Step n={1} />
                  <span>
                    В Telegram откроется <b className="font-semibold">@{start.bot}</b> — нажмите
                    «Запустить».{" "}
                    <a href={start.url} target="_blank" rel="noopener" className="text-link">
                      Не открылся?
                    </a>
                  </span>
                </li>
                <li className="flex gap-2.5">
                  <Step n={2} />
                  <span>Введите код из сообщения бота:</span>
                </li>
              </ol>
              <input
                ref={input}
                value={digits}
                onChange={(e) => {
                  const v = e.target.value.replace(/\D/g, "").slice(0, 6);
                  setCode(v);
                  if (v.length === 6) void verify(v);
                }}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="••••••"
                aria-label="Код из Telegram"
                className="field text-center font-mono !text-[28px] tracking-[0.35em]"
              />
              <Button
                className="mt-3 w-full !py-3"
                disabled={busy || digits.length !== 6}
                onClick={() => void verify(digits)}
              >
                {busy ? "Проверяю…" : "Войти"}
              </Button>
              <button
                onClick={() => void begin()}
                className="tap mt-3 w-full text-center text-[14px] text-link"
              >
                Код не пришёл — открыть бота ещё раз
              </button>
            </>
          )}
          {error && (
            <p role="alert" className="m-0 mt-3 text-center text-[14px] leading-snug text-clay">
              {error}
            </p>
          )}
        </div>
        <p className="mx-4 mt-4 text-center text-[12.5px] leading-snug text-muted">
          Войти могут только сотрудники, которых руководитель добавил в «Команду».
        </p>
      </div>
    </div>
  );
}

function Step({ n }: { n: number }) {
  return (
    <span className="mt-px grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full bg-accent text-[12px] font-semibold text-onAccent">
      {n}
    </span>
  );
}
