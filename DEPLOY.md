# Развёртывание на сервере

Всё работает в одном контейнере (`python -m app.run`): мини-апп, бот и
воркер. Контейнер перезапускается сам после сбоя и перезагрузки сервера. База
SQLite и сессия Telegram лежат в папке `data/` на сервере и переживают
пересборку.

Команды на сервере — для Ubuntu 22.04/24.04. Команды на вашем компьютере —
для PowerShell.

## 0. Что нужно

- **VPS** с Ubuntu: 1–2 ядра, 2 ГБ памяти, 20 ГБ диска (Hetzner, DigitalOcean,
  местный хостинг — любой). Доступ по SSH: `ssh root@IP-сервера`.
- **Домен — по желанию.** Без домена адрес мини-аппа даёт туннель cloudflared:
  он меняется при каждом перезапуске, но кнопка бота обновляется сама. С
  доменом адрес постоянный, HTTPS даёт Caddy.

## 1. Docker

```bash
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 2. Код

Репозиторий приватный: нужен токен GitHub с доступом на чтение
(GitHub → Settings → Developer settings → Fine-grained tokens → Contents: Read).

```bash
git clone https://<ТОКЕН>@github.com/tripowz/dovod.git ~/dovod
cd ~/dovod
git checkout claude/affectionate-turing-fmynlq   # или main, когда ветку сольют
mkdir -p data && chmod 700 data
```

## 3. Настройки

На вашем компьютере остановите Довод (Ctrl+C во всех окнах) — две копии с
одной сессией Telegram разорвут её. Скопируйте настройки и базу:

```powershell
cd "D:\SmartBooking Bitbucket\dovod\backend"
scp .env root@IP-сервера:~/dovod/.env
scp dovod.db root@IP-сервера:~/dovod/data/dovod.db
```

База переносит всё сделанное: включённые чаты, отели, задачи. Если вы
начинаете с чистой базы — второй `scp` не нужен.

На сервере проверьте `.env` (`nano ~/dovod/.env`, затем `chmod 600 ~/dovod/.env`):

- `DATABASE_URL` и `TELEGRAM_SESSION_PATH` **не трогайте** — в Docker они
  закреплены за папкой `data/`.
- `MINIAPP_URL` и `DOMAIN` — пустые (без домена) или см. шаг 6.
- База в Supabase вместо SQLite: `SERVER_DATABASE_URL=postgresql+asyncpg://…`
  (строка Session pooler, порт 5432). Таблицы и защита RLS создадутся сами.
  Данные из SQLite в Supabase сами не переносятся.

## 4. Вход в Telegram на сервере

Сессию лучше создать заново, а не копировать: вход с нового IP по чужому
файлу Telegram может счесть подозрительным и сбросить. Сотруднику придёт код —
нужен он сам или его телефон рядом.

```bash
cd ~/dovod
docker compose build
docker compose run --rm dovod python -m app.ingest.login
```

После входа удалите сессию на своём компьютере: `backend\data\dovod.session`.

## 5. Запуск

```bash
docker compose up -d
docker compose logs -f dovod     # Ctrl+C — выйти из логов, Довод продолжит работать
```

В логах — проверка настроек и строка `Довод запущен` с адресом. По этому
адресу «Довод» открывается как обычный сайт: «Войти через Telegram» → бот
присылает код → код на сайте. Без домена адрес временный (trycloudflare) и
меняется при перезапуске — для постоянного сайта нужен домен (шаг 6).

## 6. С доменом (постоянный адрес)

1. В DNS домена — запись `A`: `dovod.example.uz` → IP сервера.
2. В `.env`: `DOMAIN=dovod.example.uz` и `MINIAPP_URL=https://dovod.example.uz`.
3. Запуск с Caddy — он сам получит сертификат Let's Encrypt:

```bash
docker compose --profile https up -d
```

## Команды внутри

Всё, что раньше запускалось в PowerShell, на сервере — через `docker compose exec`:

```bash
docker compose exec dovod python -m app.chats
docker compose exec dovod python -m app.chats on all
docker compose exec dovod python -m app.hotels suggest data/hotels.csv
docker compose exec dovod python -m app.hotels apply data/hotels.csv
```

Таблицу отелей — на компьютер, поправить в Excel, обратно:

```powershell
scp root@IP-сервера:~/dovod/data/hotels.csv .
scp hotels.csv root@IP-сервера:~/dovod/data/hotels.csv
```

Догрузка истории (`app.chats history`) открывает сессию Telegram сама, поэтому
на это время Довод останавливают:

```bash
docker compose stop dovod
docker compose run --rm dovod python -m app.chats history 30
docker compose start dovod
```

## Обновление

```bash
~/dovod/deploy/update.sh
```

Забирает код, пересобирает и перезапускает (с Caddy, если в `.env` задан `DOMAIN`).

## Резервная копия

Раз в сутки, в 03:00 — копия базы в `~/dovod/backups`, хранится 14 дней:

```bash
mkdir -p ~/dovod/backups
( crontab -l 2>/dev/null; echo '0 3 * * * cd ~/dovod && docker compose exec -T dovod python -c "import sqlite3;sqlite3.connect(\"data/dovod.db\").backup(sqlite3.connect(\"data/backup.db\"))" && mv data/backup.db backups/dovod-$(date +\%F).db && find backups -name "dovod-*.db" -mtime +14 -delete' ) | crontab -
```

В Supabase копии делает сам Supabase.

## Если не работает

| Что видно | Что делать |
|---|---|
| Контейнер перезапускается по кругу | `docker compose logs dovod`: в начале — проверка настроек, пункты «НЕТ» и есть причина |
| `BOT_TOKEN не принят` | токен в `.env` неверный — взять у @BotFather |
| `Conflict: terminated by other getUpdates` | бот запущен где-то ещё (ваш компьютер) — остановить там |
| Нет кнопки мини-аппа | без домена: в логах должна быть строка `туннель: https://…`; с доменом — проверить DNS и `MINIAPP_URL` |
| `сессия Telethon не авторизована` | повторить шаг 4 |
| Caddy не получает сертификат | DNS ещё не обновился или закрыты порты 80/443 |
