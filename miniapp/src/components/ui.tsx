/** Общие элементы. Метки и «температура» вынесены сюда, потому что они
 *  повторяются на каждом экране и должны читаться одинаково везде. */

import type { ReactNode } from "react";
import type { TaskType } from "../lib/types";

export const TYPE_LABEL: Record<TaskType, string> = {
  bug: "Баг",
  problem: "Проблема",
  request: "Запрос",
  question: "Вопрос",
};

const TYPE_CLASS: Record<TaskType, string> = {
  bug: "bg-clay/12 text-clay",
  problem: "bg-ochre/12 text-ochre",
  request: "bg-link/12 text-link",
  question: "bg-muted/15 text-muted",
};

/** Цвет модуля — точка, а не заливка: восемь заливок превращают список в
 *  радугу, в которой ничего не выделяется. */
const MODULE_COLOR: Record<string, string> = {
  PMS: "#0e7c86",
  CM: "#1e4b73",
  WEBSITE: "#7a4c86",
  BOOKING: "#2c6e4c",
  MOBILE: "#b4502a",
  BILLING: "#96690a",
  API: "#5a6470",
  ADMIN: "#a03a5e",
  UNKNOWN: "#8a9aa0",
};

export function TypeChip({ type }: { type: TaskType }) {
  return (
    <span className={`rounded px-2 py-0.5 text-[10.5px] font-bold uppercase ${TYPE_CLASS[type]}`}>
      {TYPE_LABEL[type]}
    </span>
  );
}

export function ModuleChip({ module }: { module: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-black/8 bg-surface px-2 py-0.5 font-mono text-[10px] font-bold text-ink/70">
      <i
        className="block h-1.5 w-1.5 rounded-[2px]"
        style={{ background: MODULE_COLOR[module] ?? MODULE_COLOR.UNKNOWN }}
      />
      {module}
    </span>
  );
}

export function Priority({ value }: { value: number }) {
  return (
    <span className="inline-flex items-end gap-0.5" title={`Приоритет ${value} из 3`}>
      {[1, 2, 3].map((i) => (
        <i
          key={i}
          className={`block w-[3px] rounded-[1px] ${i <= value ? "bg-clay" : "bg-muted/30"}`}
          style={{ height: 3 + i * 2 }}
        />
      ))}
    </span>
  );
}

/**
 * Полоска простоя.
 *
 * Показывает дни без движения, а не приоритет: приоритет все ставят
 * «высокий», а дни простоя подделать нельзя.
 */
export function Heat({ days, overdue }: { days: number; overdue: boolean }) {
  const level = overdue ? 3 : days <= 1 ? 0 : days <= 3 ? 1 : 2;
  const color = ["bg-link", "bg-ochre", "bg-clay", "bg-clay"][level];
  const width = [22, 58, 100, 100][level];
  const text = overdue
    ? "срок прошёл"
    : days === 0
      ? "двигали сегодня"
      : days === 1
        ? "двигали вчера"
        : `${days} ${plural(days, ["день", "дня", "дней"])} без движения`;
  return (
    <div className="mt-2">
      <div className="h-[3px] overflow-hidden rounded-sm bg-muted/20">
        <i className={`block h-full rounded-sm ${color}`} style={{ width: `${width}%` }} />
      </div>
      <div className={`mt-1 text-[11px] ${level >= 2 ? "text-clay" : "text-muted"}`}>{text}</div>
    </div>
  );
}

export function plural(n: number, forms: [string, string, string]): string {
  const a = n % 10;
  const b = n % 100;
  if (a === 1 && b !== 11) return forms[0];
  if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return forms[1];
  return forms[2];
}

export function money(n: number): string {
  return n.toLocaleString("ru-RU").replace(/ /g, " ");
}

export function Card({
  children,
  onClick,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  const base = `w-full rounded-2xl border border-black/6 bg-bg p-3 text-left ${className}`;
  return onClick ? (
    <button className={base} onClick={onClick}>
      {children}
    </button>
  ) : (
    <div className={base}>{children}</div>
  );
}

export function SectionHead({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="mb-2 mt-4 flex items-baseline gap-2 px-0.5 first:mt-1">
      <h3 className="text-[11px] font-bold uppercase tracking-[0.1em] text-muted">{title}</h3>
      {right != null && <span className="ml-auto font-mono text-[11px] text-muted">{right}</span>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-5 py-11 text-center text-[13px] leading-relaxed text-muted">
      <b className="mb-1 block text-[15px] font-semibold text-ink">{title}</b>
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  className?: string;
}) {
  const styles = {
    primary: "bg-accent text-onAccent",
    ghost: "bg-surface text-ink/80 border border-black/8",
    danger: "bg-clay/12 text-clay",
  }[variant];
  return (
    <button
      className={`rounded-xl px-3 py-2 text-[13px] font-semibold disabled:opacity-40 ${styles} ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
