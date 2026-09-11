import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    // Мини-апп открывается внутри Telegram по HTTPS-адресу туннеля, а данные
    // берёт с локального бэкенда — без прокси это разные источники и CORS.
    proxy: { "/api": "http://127.0.0.1:8010" },
  },
});
