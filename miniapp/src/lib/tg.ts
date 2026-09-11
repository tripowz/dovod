/**
 * Обвязка Telegram WebApp.
 *
 * `initData` — единственное доказательство того, кто открыл приложение.
 * Он уходит в каждый запрос заголовком и проверяется на сервере: доверять
 * тому, что фронт присылает про себя сам, нельзя.
 */

type HapticStyle = "light" | "medium" | "heavy" | "soft" | "rigid";

interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  MainButton: {
    setText(t: string): void;
    show(): void;
    hide(): void;
    enable(): void;
    disable(): void;
    onClick(cb: () => void): void;
    offClick(cb: () => void): void;
  };
  BackButton: {
    show(): void;
    hide(): void;
    onClick(cb: () => void): void;
    offClick(cb: () => void): void;
  };
  HapticFeedback: {
    impactOccurred(style: HapticStyle): void;
    notificationOccurred(type: "error" | "success" | "warning"): void;
  };
  showAlert(message: string, cb?: () => void): void;
  showConfirm(message: string, cb: (ok: boolean) => void): void;
  openTelegramLink(url: string): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

export const tg = window.Telegram?.WebApp;

/** Приложение открыто вне Telegram — там нет подписи и работать нечему. */
export const outsideTelegram = !tg || !tg.initData;

export function initTelegram(): void {
  if (!tg) return;
  tg.ready();
  tg.expand();
}

export function haptic(style: HapticStyle = "light"): void {
  tg?.HapticFeedback.impactOccurred(style);
}

export function notify(type: "error" | "success" | "warning"): void {
  tg?.HapticFeedback.notificationOccurred(type);
}

export class ApiError extends Error {
  constructor(
    message: string,
    /** 409 — нарушено правило, а не сломался сервер. Это разные сообщения. */
    readonly status: number,
  ) {
    super(message);
  }

  get isRule(): boolean {
    return this.status === 409 || this.status === 403;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": tg?.initData ?? "",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      // FastAPI кладёт текст в detail; у 422 это массив описаний полей.
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail.length)
        detail = body.detail[0]?.msg ?? detail;
    } catch {
      /* тело не JSON — оставляем код */
    }
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body: unknown) =>
    request<T>(p, { method: "PATCH", body: JSON.stringify(body) }),
};
