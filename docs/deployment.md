# Deployment

The server is a single stateless container. State lives in **PostgreSQL** (source of truth for
everything) and **Redis** (rate-limit counters, config cache, leaderboard indexes — all
rebuildable, so Redis persistence is optional).

## Production checklist

| | |
|---|---|
| `LIVEOPS_ENV=prod` | Server refuses to start with default secrets |
| `LIVEOPS_ADMIN_TOKEN` | Long random string (`openssl rand -hex 32`), or `""` to disable it once named owners exist (`python -m app.cli create-admin`) |
| `LIVEOPS_JWT_SECRET` | ≥ 32 random bytes. Rotating it logs every player out (SDKs re-login transparently) |
| `LIVEOPS_DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` |
| `LIVEOPS_REDIS_URL` | `redis://host:6379/0` |
| `FORWARDED_ALLOW_IPS` | IP/CIDR of your reverse proxy, so rate limits see real client IPs |
| `WEB_CONCURRENCY` | Uvicorn workers per container (default 2; ~1 per CPU core) |
| TLS | Terminate at the reverse proxy / load balancer |
| Body size | Limit request bodies at the proxy (e.g. nginx `client_max_body_size 512k;`) — cloud saves are checked after parsing |
| Admin access | Consider exposing `/admin` and `/ui` only on an internal network or VPN |

Migrations run automatically on container start (`alembic upgrade head`). With several replicas,
run them as a separate one-off job instead and start replicas with
`uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers`.

## Example: docker compose behind nginx

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment: {POSTGRES_USER: liveops, POSTGRES_PASSWORD: ${PG_PASSWORD}, POSTGRES_DB: liveops}
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: {test: ["CMD-SHELL", "pg_isready -U liveops"], interval: 5s, retries: 20}
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--maxmemory-policy", "noeviction"]
  server:
    image: ghcr.io/oxxxyzed/fastapi-liveops:latest   # or build: .
    environment:
      LIVEOPS_ENV: prod
      LIVEOPS_ADMIN_TOKEN: ${ADMIN_TOKEN}
      LIVEOPS_JWT_SECRET: ${JWT_SECRET}
      LIVEOPS_DATABASE_URL: postgresql+asyncpg://liveops:${PG_PASSWORD}@postgres:5432/liveops
      LIVEOPS_REDIS_URL: redis://redis:6379/0
      FORWARDED_ALLOW_IPS: "*"        # only nginx can reach this container
    depends_on: {postgres: {condition: service_healthy}, redis: {condition: service_started}}
  nginx:
    image: nginx:1.27-alpine
    ports: ["443:443"]
    volumes: ["./nginx.conf:/etc/nginx/conf.d/default.conf:ro", "./certs:/certs:ro"]
volumes: {pgdata: {}}
```

```nginx
server {
  listen 443 ssl;
  ssl_certificate /certs/fullchain.pem;
  ssl_certificate_key /certs/privkey.pem;
  client_max_body_size 512k;
  location / {
    proxy_pass http://server:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

Redis must not evict keys (`noeviction` or a large enough `maxmemory`): evicted leaderboard
indexes are rebuilt from Postgres, but evicted rate-limit counters reset early.

## Background worker

Every API process runs a small worker loop (scheduled publishes, webhook delivery). Jobs use
`SKIP LOCKED`, so running it in all replicas is safe. To isolate it, set `LIVEOPS_RUN_WORKER=false`
on API replicas and run one extra replica with it enabled.

Webhook URLs are configured by admins (editor role) and requested from the server: if your server
can reach internal services, restrict outbound traffic at the network level.

## Scaling notes

- The API is stateless — scale horizontally behind a load balancer.
- Hot paths: `GET /v1/config` is served from a Redis snapshot (5 min TTL, invalidated on publish);
  leaderboard reads are Redis-only; score submits and saves are one Postgres upsert each.
- Postgres connection pool is per worker (SQLAlchemy default: 5 + 10 overflow). Put PgBouncer in
  front when running many workers/replicas.

## Performance

Indicative numbers from `loadtest/locustfile.py` — 300 concurrent simulated players, server with
4 workers and Postgres, Redis and Locust on the **same 4-vCPU machine**, 60 s:

| Endpoint | Requests | p50 | p95 | Errors |
|---|---:|---:|---:|---:|
| `GET /v1/config` | 6 300 | 5 ms | 14 ms | 0 |
| `POST …/scores` | 3 260 | 11 ms | 24 ms | 0 |
| `GET …/leaderboards/{key}` | 1 875 | 8 ms | 18 ms | 0 |
| `GET …/me` | 1 310 | 8 ms | 21 ms | 0 |
| `PUT /v1/saves/{slot}` | 1 271 | 10 ms | 23 ms | 0 |

Throughput here is bounded by the simulated players' think time (~240 req/s total), not the
server. Run the load test against your own infrastructure before relying on these figures.
