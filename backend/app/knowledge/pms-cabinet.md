---
module: PMS
kind: architecture
title: Кабинет отельера и PMS — брони, календарь, тарифы, номерной фонд
source: smartbooking-api app/Http/Controllers/Hotelier/*; smartbooking-hotel (Angular) src/app/modules/hotel
---

Ядро PMS в smartbooking-api — контроллеры `Hotelier/`. По ним видно состав функций кабинета:

- Брони и календарь: `BookingController`, `CalendarController`, `BookingNotesController`, `BlockHoldController`, `HoldController`, `AvailableRoomTypesController`, `ImportReservationController`.
- Номерной фонд и объект: `PropertyController`, `BuildingController`, `PropertyFeaturesController`, `PropertyImagesController`, `ChannelRoomTypesController`.
- Тарифы и цены: `RateController`, `PropertyPricesController`, `DiscountController`, `PromoCodeController`, `CompanyRangeController`.
- Гости: `GuestController`, `GuestNotesController`.
- Хозчасть: `HouseKeepersController`, `CashDrawersController`.
- Каналы из кабинета: `PropertyChannelController`, `ChannelPushReservationController` (см. знание про CM).

Веб-кабинет — **smartbooking-hotel** (Angular 9), модули `src/app/modules/` (auth, hotel, payment, confidentiality, specific). Это фронт к тем же эндпоинтам.

Вывод для диагноста: «в календаре не то / бронь не редактируется / тариф не применился» — это PMS. Если данные неверны на бэке — смотри соответствующий `Hotelier/*Controller`; если данные верны, а в кабинете показывается не так — смотри Angular-модуль `hotel`.
