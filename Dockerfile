# Builds the API server with the admin UI bundled at /ui.
FROM node:22-alpine AS admin
WORKDIR /admin
COPY admin/package.json admin/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY admin/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/ ./
COPY --from=admin /admin/dist /app/admin-ui
RUN useradd -r -u 10001 liveops && chown -R liveops /app
USER liveops
ENV PATH="/app/.venv/bin:$PATH" LIVEOPS_ADMIN_UI_DIR=/app/admin-ui \
    FORWARDED_ALLOW_IPS=127.0.0.1 WEB_CONCURRENCY=2
EXPOSE 8000
# FORWARDED_ALLOW_IPS: set to your reverse proxy address(es) so client IPs (used for rate
# limiting) come from X-Forwarded-For. Uvicorn reads it and WEB_CONCURRENCY from the env.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
