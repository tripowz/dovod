/** @type {import('tailwindcss').Config} */

// Цвета темы Telegram приходят CSS-переменными, и Tailwind сам не умеет
// делать из них полупрозрачные оттенки (`bg-link/10` молча не генерировался).
// color-mix даёт прозрачность любому цвету, в том числе переменной.
const tint = (cssVar, fallback) => ({ opacityValue }) =>
  opacityValue === undefined || opacityValue === "1"
    ? `var(${cssVar}, ${fallback})`
    : `color-mix(in srgb, var(${cssVar}, ${fallback}) calc(${opacityValue} * 100%), transparent)`;

const solid = (hex) => ({ opacityValue }) =>
  opacityValue === undefined || opacityValue === "1"
    ? hex
    : `color-mix(in srgb, ${hex} calc(${opacityValue} * 100%), transparent)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Палитра — из темы Telegram, запасные значения — системные цвета iOS:
        // приложение должно выглядеть родным и в светлой, и в тёмной теме.
        bg: tint("--tg-theme-bg-color", "#ffffff"),
        surface: tint("--tg-theme-secondary-bg-color", "#f2f2f7"),
        ink: tint("--tg-theme-text-color", "#000000"),
        muted: tint("--tg-theme-hint-color", "#8e8e93"),
        link: tint("--tg-theme-link-color", "#007aff"),
        accent: tint("--tg-theme-button-color", "#007aff"),
        onAccent: tint("--tg-theme-button-text-color", "#ffffff"),
        // Тонкие разделители и серые заливки iOS. Строятся от цвета текста,
        // поэтому одинаково уместны в светлой и тёмной теме.
        sep: "color-mix(in srgb, var(--tg-theme-text-color, #000) 12%, transparent)",
        fill: "color-mix(in srgb, var(--tg-theme-hint-color, #8e8e93) 16%, transparent)",
        clay: solid("#e5342a"),
        ochre: solid("#e08600"),
        moss: solid("#248a3d"),
      },
      opacity: {
        6: "0.06",
        8: "0.08",
        12: "0.12",
        15: "0.15",
        55: "0.55",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Text",
          "Inter",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: ["SF Mono", "ui-monospace", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
