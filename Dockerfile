# syntax=docker/dockerfile:1
# «Довод» одним образом: мини-апп, API, бот и воркер (python -m app.run).

# --- сборка мини-аппа ---
FROM node:20-alpine AS web
WORKDIR /web
COPY miniapp/package.json miniapp/package-lock.json ./
# extra_ca — необязательный сертификат прокси (корпоративная сеть):
#   docker build --secret id=extra_ca,src=/path/ca.crt .
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY miniapp/ ./
RUN npm run build

# --- бэкенд ---
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8010 \
    DATABASE_URL=sqlite+aiosqlite:///./data/dovod.db \
    TELEGRAM_SESSION_PATH=./data/dovod.session

# cloudflared — запасной путь без домена: app.run сам поднимает туннель и
# ставит его адрес в кнопку бота. С доменом (Caddy) он просто не запускается.
ARG CLOUDFLARED_VERSION=2025.8.1
ARG TARGETARCH
ADD --chmod=755 \
    https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${TARGETARCH} \
    /usr/local/bin/cloudflared

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/miniapp/dist

# База SQLite и сессия Telegram — здесь; папка монтируется с сервера.
VOLUME ["/app/backend/data"]
EXPOSE 8010
HEALTHCHECK --interval=60s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"API_PORT\",\"8010\")}/health',timeout=4)"
CMD ["python", "-m", "app.run"]
