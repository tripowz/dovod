---
module: API
kind: architecture
title: Бэкенд-основа, авторизация, вебхуки, админка
source: smartbooking-api routes/api.php, app/Http/Controllers/{Auth,Admin,SuperAdmin,Webhook}, app/Domain/AIAgent
---

**smartbooking-api** (Laravel) — основа всей системы. Ключевое для разбора:

- **Авторизация**: Laravel Passport. Приложение получает токен (`auth/app`), пользователь входит по коду (`auth/user/login` → `verify`). Мидлвары `auth.api.app`, `auth:api`.
- **Вебхуки** (`Controllers/Webhook`): платежи (Montra, Octo), мессенджеры OTA (Booking.com, Expedia). Общий `WebhookController`. Входящая точка почти всех интеграций — если «извне что-то не дошло», начинать здесь.
- **Админка/суперадмин**: `Controllers/Admin`, `Controllers/SuperAdmin` — внутренние инструменты SmartBooking (не кабинет отельера).
- **AIAgent**: в API уже есть собственный `Domain/AIAgent` (роутер `AIAgentRouter`, доменные инструменты Analytics/Settings, контроллер `Hotelier/AIAgentController`). Полезно знать: у системы уже есть ИИ-слой, и агенты Довода — внешние по отношению к нему.

Вывод для диагноста: модуль `API` ставь, когда симптом не про конкретный экран, а про интеграцию, токен/вход или данные на бэке. Это не отдельный «продукт» для клиента, а слой, на котором держатся PMS, CM, BILLING и мобильное.
