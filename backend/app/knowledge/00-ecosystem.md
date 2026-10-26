---
module: UNKNOWN
kind: architecture
title: Экосистема SmartBooking — какие репозитории за что отвечают
source: обзор рабочих репозиториев smartbooking-uz-*
---

SmartBooking — сервис автоматизации гостиниц в Узбекистане. Система распределена по нескольким репозиториям; при разборе обращения важно понять, в чей код смотреть.

- **smartbooking-api** (Laravel, PHP) — главный бэкенд. Здесь брони, номерной фонд, тарифы, интеграции с каналами (OTA), вебхуки платежей и мессенджеров, авторизация (Passport), а также внутренний `Domain/AIAgent`. Почти любой баг данных или интеграции начинается здесь.
- **smartbooking-hotel** (Angular 9) — веб-кабинет отельера: календарь, брони, тарифы, настройки каналов, финансы. Фронт к smartbooking-api.
- **sb-app-2022** (React Native) — мобильное приложение для отельеров. Firebase (пуши, аналитика, remote-config). Тот же API.
- **site-generator** + **smartbooking-hotel-website** (Nx monorepo, React) — шаблоны и конструктор сайтов отелей.
- **smart-sites** (Laravel 12 + PostgreSQL) — бэкенд сервиса сайтов отелей.
- **smart-bot** (Laravel 12) — Telegram-бот.
- **smartcard** (Lumen) — отдельный сервис (смарт-карта/лояльность).
- **smartbooking-tour-layout** (Angular) — продукт туров.
- **sb-hotel-test-ui** (Selenium, Python) — e2e-тесты: авторизация, матрица доступности, бронь номера, сравнение с конкурентами, дашборд. Полезно как список реальных сценариев.

Правило разбора: симптом у отельера (не приходит бронь, не считается тариф, не уходит пуш) — смотри smartbooking-api; «кнопка в кабинете не работает / не то показывает» — smartbooking-hotel (Angular) или sb-app-2022 (мобильное); «сайт отеля» — site-generator/smart-sites.
