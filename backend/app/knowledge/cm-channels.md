---
module: CM
kind: architecture
title: Channel manager — как брони и доступность ходят между OTA и системой
source: smartbooking-api: routes/api.php, app/Http/Controllers/Hotelier/ChannelPushReservationController.php, app/Console/Commands/Channel/*
---

Синхронизация с OTA в smartbooking-api устроена двумя способами, и это важно для разбора: часть каналов **пушит** брони на наш эндпоинт, часть мы **забираем** сами.

**Push (канал присылает бронь нам), контроллер `Hotelier\ChannelPushReservationController`:**
- Ostrovok → `POST property/push-reservation`, middleware `push-reservation-ostrovok`, метод `ostrovok`.
- Expedia → `property/push-reservation/expedia`, метод `expedia`.
- Bronevik → `property/push-reservation/bronevik`.
- Trip.com → `property/push-reservation/tripcom`.
- HotelRunner → `callback` (метод `hr`).

**Pull (мы сами забираем), консольные команды `app/Console/Commands/Channel/` и рядом:**
- Booking.com → `GetBookingComReservationCommand`, `RetrieveBookingComReservationsCommand`, `UpdateBookingComAvailabilityCommand`, `BookingComAuthCommand`.
- MyBooking → `RetrieveMyBookingReservationsCommand`.
- Общая обработка → `UpdateReservationChannel`.

**OTA-чат** (сообщения гостя из OTA) — отдельные вебхуки: `BookingComMessagingWebhookController`, `ExpediaMessagingWebhookController`, плюс `PullChatMessagesCommand`.

Вывод для диагноста: Ostrovok/Expedia/Bronevik/Trip.com — это **входящий вебхук**, и если бронь «не пришла», она либо не дошла до эндпоинта, либо упала в его обработке (смотри middleware `push-reservation-ostrovok` и метод контроллера). Booking.com/MyBooking — это **наш крон/команда**, и там симптом «не приходит» чаще значит, что не отработала команда забора.
