# yt-dlp Bot — Operations Guide (English)

## Overview

Self-hosted Telegram and Discord media download bot. Users submit public HTTP(S) URLs; the service produces MP4/MP3 (or playlist ZIP) artifacts, reports progress, supports cancel/status, uploads directly under the platform limit, otherwise issues reusable signed range download links.

## Requirements

- Linux host with Docker and Docker Compose
- CPython 3.13 + [uv](https://github.com/astral-sh/uv) for development
- FFmpeg / ffprobe (image-bundled for deployment)
- Operator-chosen `capacity_bytes` within host disk limits

## Quick start (development)

```bash
uv sync --all-extras --frozen
uv run ruff format --check
uv run ruff check
uv run pyright
uv run pytest
```

## Configuration

Copy `config.example.toml` to a secure path (not world-readable). Resolve secrets via:

- `env:VAR_NAME`
- `file:/run/secrets/...`

Required highlights:

- `storage.capacity_bytes` — operator selected (example value is illustrative)
- `artifacts.public_base_url` — HTTPS, no query/fragment/trailing slash
- `artifacts.signing_secret_ref` — ≥ 32 bytes entropy
- At least one platform enabled with a token secret
- Static `access.administrators` cannot be changed via chat

## Deployment

```bash
docker compose config
docker compose build
# Provide secret files under ./secrets before up
docker compose up -d
```

Health: private `/healthz` (liveness) and `/readyz` (readiness). Public download origin serves only `/v1/artifacts/{id}/{name}`.

## Security notes

- Controlled egress is mandatory; URL validation alone is insufficient.
- Logs must never contain bot tokens, signing secrets, complete bearer URLs, or sensitive source URL components.
- Run the app as non-root with a read-only root filesystem expectation.

## Alerts and runbooks

| Signal | Meaning | Operator action |
| --- | --- | --- |
| `/readyz` not ready | Admission closed | Inspect logs for recovery/egress/storage; fix config or disk |
| Capacity denials rising | Storage near limit | Raise `capacity_bytes` with admin confirmation, or free disk |
| Cleanup last_error set | Deletion retry stuck | Check filesystem permissions and artifact leases |
| Worker spawn failures | Media pipeline unhealthy | Verify FFmpeg/yt-dlp in image; enable fixture mode only for CI |

Backup: stop writers, copy SQLite + WAL/SHM under `state/`, copy `data/artifacts/`. Restore onto empty volumes before starting.

Upgrade: pull image, `docker compose up -d`, confirm `/readyz`, run controlled live smoke.

## Live smoke (manual)

With real credentials (not part of routine CI): submit `/ytdl` and `/ytmp3` on both platforms, verify progress, cancel, status, direct upload below limit, signed link above limit, and restart reconciliation.

Release acceptance: deterministic gates live in `.github/workflows/ci.yml`. Local agent status: `doc/current_progress.md`. Historical AC checkbox ledger: `doc/archive/tasks/progress.md` (archived; not open work).
