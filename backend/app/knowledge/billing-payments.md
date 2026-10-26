---
module: BILLING
kind: architecture
title: Счета, платежи и онлайн-оплата
source: smartbooking-api Hotelier/ChargesController, FinancialController, OnlinePaymentController, PaymentTypesController; Webhook/MontraWebhookController, OctoWebhookController
---

Денежная часть в smartbooking-api:

- Счета и начисления гостю: `ChargesController`, `ProductController`, `ProductCategoryController`, `PaymentTypesController`.
- Финансы по объекту и касса: `FinancialController`, `CashDrawersController`.
- Онлайн-оплата: `OnlinePaymentController`. Провайдеры (локальные для UZ) приходят через вебхуки: `Webhook\MontraWebhookController`, `Webhook\OctoWebhookController`.

Вывод для диагноста: «оплата прошла, а в системе не отметилась» — это почти всегда **вебхук провайдера** (Montra/Octo): проверять, дошёл ли колбэк и как он обработан, по аналогии с каналами OTA. «Счёт считается неверно / не та сумма» — логика начислений (`ChargesController`/`FinancialController`), а не платёжный провайдер. Разделять эти две ветки до передачи инженеру — иначе разбирают не тот модуль.

Отдельно: задолженности отелей перед SmartBooking — это биллинг самого сервиса, не гостевые платежи; не путать.
