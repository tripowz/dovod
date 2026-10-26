---
module: MOBILE
kind: architecture
title: Мобильное приложение отельера (React Native)
source: sb-app-2022 (React Native, Firebase); smartbooking-api Controllers/Mobile, Auth/PassportAuthController
---

**sb-app-2022** — приложение для отельеров на React Native. Firebase: пуши (messaging), аналитика, crashlytics, remote-config. Работает против того же smartbooking-api.

Авторизация (из `routes/api.php`): вход по коду — `auth/user/login` → `login/verify` (подтверждение кодом) → токен Passport, есть `refresh-token` и `create-password`. На бэке — `Auth\PassportAuthController`, плюс отдельные контроллеры `Controllers/Mobile`.

Вывод для диагноста: «в приложении не вижу то, что есть в вебе» — чаще расхождение версий/кэша приложения или мобильные эндпоинты (`Controllers/Mobile`), а не общий баг данных (тогда он был бы и в вебе). «Не приходит пуш» — Firebase messaging + remote-config, а не PMS. «Не могу войти / не приходит код» — ветка `auth/user/login*` и SMS-код, смотреть и бэк (PassportAuthController), и доставку кода.
