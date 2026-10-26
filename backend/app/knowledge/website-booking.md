---
module: WEBSITE
kind: architecture
title: Сайты отелей и модуль бронирования
source: site-generator (Nx/React), smartbooking-hotel-website (Nx), smart-sites (Laravel 12 + PostgreSQL); smartbooking-api Controllers/BookingEngine
---

Сайты отелей — отдельный пласт:

- **site-generator** и **smartbooking-hotel-website** — Nx-монорепо с шаблонами сайтов на React (Radix UI). Здесь вёрстка и конструктор страниц отеля (`apps/`, `libs/`, `ui-kit/`).
- **smart-sites** — бэкенд сервиса сайтов на Laravel 12 + PostgreSQL (отдельная база от основного API).
- **Модуль бронирования на сайте** (booking engine) живёт в smartbooking-api: контроллеры `BookingEngine`, домен `Domain/BookingEngine`. Это то, что превращает посетителя сайта в бронь в системе.

Вывод для диагноста: «на сайте отеля что-то сломано/не так выглядит» — site-generator/smart-sites. «Не могу забронировать на сайте / бронь с сайта не создалась» — это booking engine в smartbooking-api (`BookingEngine`), а не шаблон сайта. Разделять витрину (шаблон) и движок брони (API) — разные репозитории и разные люди.
