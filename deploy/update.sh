#!/usr/bin/env sh
# Обновление на сервере: забрать код, пересобрать, перезапустить.
set -e
cd "$(dirname "$0")/.."
git pull --ff-only
if grep -q '^DOMAIN=.\+' .env 2>/dev/null; then
  docker compose --profile https up -d --build
else
  docker compose up -d --build
fi
docker image prune -f >/dev/null
docker compose logs --tail 30 dovod
