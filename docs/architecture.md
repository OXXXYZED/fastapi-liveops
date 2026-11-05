# Architecture

```
 game client ──(SDK)──►  FastAPI  ──►  PostgreSQL   source of truth
 admin UI   ──(/ui)───►  (stateless)──► Redis        cache, indexes, rate limits
```

## Data model

```
project ─┬─ environment ─┬─ api_key          (client | server, sha256-hashed)
         │               ├─ player           (device_id unique per environment)
         │               │     ├─ cloud_save (slot, version, jsonb)
         │               │     └─ leaderboard_score (board, season, score)
         │               ├─ config_version   (append-only, highest = live)
         │               ├─ feature_flag     (enabled, rollout %, rules jsonb)
         │               └─ leaderboard      (sort, update mode, reset schedule, bounds)
```

Everything is scoped to an **environment**; deleting a project cascades.

## Auth

- **Admin**: named accounts (scrypt-hashed passwords, HS256 session JWT with `typ=admin`) or the
  static `LIVEOPS_ADMIN_TOKEN` (acts as owner; compared in constant time). The user row is loaded on
  every request, so disabling a user or changing a role takes effect immediately. Reads need
  `viewer`, writes `editor`; project deletion and user management need `owner`. Successful writes
  are appended to `audit_log`.
- **API keys**: `lo_<kind>_<256-bit random>`, stored as SHA-256 (high-entropy keys don't need a slow hash;
  the hash is indexed for O(1) lookup). Client keys are public by design — they only allow
  creating/resuming players.
- **Players**: HS256 JWT with `sub` = player id and `env` = environment id. Every player query
  filters by both, so a token can never reach another environment's data.

## Consistency decisions

| Feature | Mechanism |
|---|---|
| Device login | `INSERT … ON CONFLICT DO UPDATE … RETURNING (xmax = 0)` — atomic, reports whether the player was created |
| Config publish | `version = max + 1` guarded by a unique constraint; optional `base_version` for optimistic locking |
| Flag evaluation | Pure function; bucket = `sha256("{flag}:{player}") % 100`, so rollouts are stable per player and independent across flags |
| Score submit | Postgres upsert computes the merged score (`GREATEST` / `LEAST` / `+` / overwrite); Redis `ZADD` happens while the row lock is held, so Redis sees updates in commit order |
| Leaderboard index | Rebuilt from Postgres when the ZSET is missing (Redis flush, season TTL expiry) |
| Cloud save | Conditional `UPDATE … WHERE version = N` / `INSERT … ON CONFLICT DO NOTHING`; slot-count limit under a per-player `pg_advisory_xact_lock` |
| Rate limiting | Redis fixed window, per client key + IP, per server key, per player |

## SDKs

Unity (C#) and Dart share one design: an `ITransport`/`http.Client` seam, a key-value store for
the session and offline config cache, lazy login with single-flight, token refresh 60 s before
expiry, and one automatic re-login on `401`. Both are tested end-to-end against a live server in CI.
