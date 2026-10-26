/** Общие элементы. Метки и «температура» вынесены сюда, потому что они
 *  повторяются на каждом экране и должны читаться одинаково везде.
 *  Стиль — минимализм iOS: сгруппированные списки, тонкие разделители,
 *  серые заливки вместо рамок. */

import { Children, Fragment } from "react";
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
  problem: "bg-ochre/15 text-ochre",
  request: "bg-link/12 text-link",
  question: "bg-fill text-muted",
};

/** Цвет модуля — точка, а не заливка: восемь заливок превращают список в
 *  радугу, в которой ничего не выделяется. */
const MODULE_COLOR: Record<string, string> = {
  PMS: "#007aff",
  CM: "#5856d6",
  WEBSITE: "#af52de",
  BOOKING: "#34c759",
  MOBILE: "#ff9500",
  BILLING: "#ffcc00",
  API: "#8e8e93",
  ADMIN: "#ff2d55",
  UNKNOWN: "#c7c7cc",
};

/** Нейтральная метка-«пилюля». */
export function Tag({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-[3px] text-[11px] font-semibold leading-none ${className}`}
    >
      {children}
    </span>
  );
}

export function TypeChip({ type }: { type: TaskType }) {
  return <Tag className={TYPE_CLASS[type]}>{TYPE_LABEL[type]}</Tag>;
}

export function ModuleChip({ module }: { module: string }) {
  return (
    <Tag className="bg-fill font-mono !text-[10.5px] text-ink/70">
      <i
        className="block h-1.5 w-1.5 rounded-full"
        style={{ background: MODULE_COLOR[module] ?? MODULE_COLOR.UNKNOWN }}
      />
      {module}
    </Tag>
  );
}

export function Priority({ value }: { value: number }) {
  return (
    <span className="inline-flex items-end gap-0.5" title={`Приоритет ${value} из 3`}>
      {[1, 2, 3].map((i) => (
        <i
          key={i}
          className={`block w-[3px] rounded-full ${i <= value ? "bg-clay" : "bg-muted/30"}`}
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
  const color = ["bg-moss", "bg-ochre", "bg-clay", "bg-clay"][level];
  const width = [22, 58, 100, 100][level];
  const text = overdue
    ? "срок прошёл"
    : days === 0
      ? "двигали сегодня"
      : days === 1
        ? "двигали вчера"
        : `${days} ${plural(days, ["день", "дня", "дней"])} без движения`;
  return (
    <div className="mt-2 flex items-center gap-2">
      <div className="h-1 w-14 shrink-0 overflow-hidden rounded-full bg-fill">
        <i className={`block h-full rounded-full ${color}`} style={{ width: `${width}%` }} />
      </div>
      <div className={`text-[12px] ${level >= 2 ? "text-clay" : "text-muted"}`}>{text}</div>
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
  return n.toLocaleString("ru-RU").replace(/ /g, " ");
}

/** Отдельная карточка: белая плашка со скруглением, без рамки. */
export function Card({
  children,
  onClick,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  const base = `block w-full rounded-[14px] bg-bg p-3.5 text-left ${className}`;
  return onClick ? (
    <button className={`${base} tap`} onClick={onClick}>
      {children}
    </button>
  ) : (
    <div className={base}>{children}</div>
  );
}

/** Сгруппированный список, как в Настройках iOS: строки в одной плашке,
 *  между ними тонкий разделитель с отступом слева. */
export function Group({ children, className = "" }: { children: ReactNode; className?: string }) {
  const items = Children.toArray(children);
  return (
    <div className={`overflow-hidden rounded-[14px] bg-bg ${className}`}>
      {items.map((child, i) => (
        <Fragment key={i}>
          {i > 0 && <div className="ml-4 h-[0.5px] bg-sep" />}
          {child}
        </Fragment>
      ))}
    </div>
  );
}

/** Строка сгруппированного списка. С onClick — нажимается и получает шеврон. */
export function Row({
  children,
  onClick,
  right,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  right?: ReactNode;
  className?: string;
}) {
  const inner = (
    <>
      <div className="min-w-0 flex-1">{children}</div>
      {right}
      {onClick && <Chevron />}
    </>
  );
  const base = `flex w-full items-center gap-3 px-4 py-3 text-left ${className}`;
  return onClick ? (
    <button className={`${base} tap`} onClick={onClick}>
      {inner}
    </button>
  ) : (
    <div className={base}>{inner}</div>
  );
}

export function Chevron() {
  return (
    <svg width="8" height="13" viewBox="0 0 8 13" className="shrink-0 text-muted/60" aria-hidden>
      <path
        d="M1.5 1.5 6.5 6.5 1.5 11.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Сегментный переключатель iOS. */
export function Segmented<K extends string | number>({
  value,
  options,
  onChange,
  className = "",
}: {
  value: K;
  options: { key: K; label: ReactNode; disabled?: boolean }[];
  onChange: (k: K) => void;
  className?: string;
}) {
  return (
    <div
      className={`grid gap-0.5 rounded-[9px] bg-fill p-0.5 ${className}`}
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      {options.map((o) => (
        <button
          key={String(o.key)}
          disabled={o.disabled}
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
          className={`flex items-center justify-center gap-1.5 rounded-[7px] px-2 py-[6px] text-[13px] font-semibold leading-tight transition-colors disabled:opacity-35 ${
            value === o.key
              ? "bg-bg text-ink shadow-[0_1px_3px_rgba(0,0,0,0.12),0_0_0_0.5px_rgba(0,0,0,0.04)]"
              : "text-ink/60"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Красный счётчик-«бейдж», как на иконках iOS. */
export function Badge({ n }: { n: number }) {
  if (n <= 0) return null;
  return (
    <em className="min-w-[18px] rounded-full bg-clay px-1.5 text-center text-[11px] font-semibold not-italic leading-[18px] text-white">
      {n}
    </em>
  );
}

export function SectionHead({
  title,
  right,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mb-1.5 mt-6 flex items-baseline gap-2 px-4 ${className}`}>
      <h3 className="m-0 text-[13px] font-normal uppercase tracking-[0.02em] text-muted">
        {title}
      </h3>
      {right != null && <span className="ml-auto text-[13px] text-muted">{right}</span>}
    </div>
  );
}

/** Сноска под группой — мелкий серый текст, как пояснения в Настройках. */
export function Note({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <p className={`mx-4 mb-0 mt-2 text-[12.5px] leading-snug text-muted ${className}`}>
      {children}
    </p>
  );
}

/** Подпись над полем формы. */
export function FieldLabel({ children }: { children: ReactNode }) {
  return <span className="mb-1.5 block px-1 text-[13px] text-muted">{children}</span>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-6 py-12 text-center text-[14px] leading-relaxed text-muted">
      <b className="mb-1 block text-[17px] font-semibold text-ink">{title}</b>
      {children}
    </div>
  );
}

export function Loading() {
  return (
    <div className="flex justify-center py-14">
      <span className="h-6 w-6 animate-spin rounded-full border-[2.5px] border-fill border-t-muted" />
    </div>
  );
}

/** Возврат к списку — синий текст с шевроном, как в навигации iOS. */
export function Back({ onClick, children = "Назад" }: { onClick: () => void; children?: ReactNode }) {
  return (
    <button onClick={onClick} className="tap -ml-1 flex items-center gap-1 self-start text-[17px] text-link">
      <svg width="12" height="20" viewBox="0 0 12 20" aria-hidden>
        <path
          d="M10 2 2 10l8 8"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {children}
    </button>
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
    ghost: "bg-link/10 text-link",
    danger: "bg-clay/10 text-clay",
  }[variant];
  return (
    <button
      className={`tap rounded-[12px] px-4 py-2.5 text-[15px] font-semibold disabled:opacity-40 ${styles} ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

/** Выбор одного варианта из нескольких — «пилюли» с заливкой у выбранной. */
export function Choice({
  on,
  onClick,
  children,
  className = "",
}: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`tap rounded-full px-3.5 py-[7px] text-[14px] font-medium ${
        on ? "bg-accent text-onAccent" : "bg-bg text-ink"
      } ${className}`}
    >
      {children}
    </button>
  );
}
