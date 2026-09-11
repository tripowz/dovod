/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Палитра берётся из темы Telegram через CSS-переменные: приложение
        // должно выглядеть родным и в светлой, и в тёмной теме клиента.
        bg: "var(--tg-theme-bg-color, #ffffff)",
        surface: "var(--tg-theme-secondary-bg-color, #f3f6f5)",
        ink: "var(--tg-theme-text-color, #0f1b22)",
        muted: "var(--tg-theme-hint-color, #6c838d)",
        link: "var(--tg-theme-link-color, #0e7c86)",
        accent: "var(--tg-theme-button-color, #0e7c86)",
        onAccent: "var(--tg-theme-button-text-color, #ffffff)",
        clay: "#b4502a",
        ochre: "#96690a",
        moss: "#2c6e4c",
      },
      fontFamily: {
        sans: ["Onest", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
