# Changelog

## Unreleased

- Admin accounts (scrypt passwords, JWT sessions) with `viewer` / `editor` / `owner` roles,
  last-owner protection, login rate limiting, `python -m app.cli create-admin`.
- Audit log of all successful admin writes, with UI.

## 0.1.0 — 2026-11-05

First public release.

- **Server**: projects, environments, client/server API keys, anonymous/device player auth (JWT),
  rate limiting, remote config with versioning and rollback, feature flags with targeting and
  percentage rollouts, seasonal leaderboards, cloud save with optimistic concurrency.
- **Admin UI** served at `/ui`.
- **Unity SDK** (`com.oxxxyzed.liveops`) and **Dart/Flutter SDK** (`liveops_client`).
- Docker image, docker compose, deployment guide, load test.
