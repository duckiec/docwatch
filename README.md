# DocWatch

DocWatch monitors Docker containers, records crash events, classifies failures, and sends alerts.

## Quick Start

1. Copy `.env.example` to `.env`.
2. Pull the published image and start the stack:

```bash
docker compose pull
docker compose up -d
```

3. Open `http://localhost:8080`.

Optional: build locally instead of using the published image.

```bash
docker compose up --build
```

## What It Tracks

- container status changes (`running` -> `exited`)
- restart count increases
- exit code, uptime, and recent logs
- crash type classification (`OOM`, `Exit 1`, `Exit 137`, `Network`, `Config error`, `Clean exit`, `Unknown`)

## Useful Features

- Manual scan now from UI and API (`POST /api/refresh`)
- Crash type summary endpoint (`GET /api/crash-types`)
- 24h timeline endpoint for trend charting (`GET /api/timeline`)
- CSV export for incident history (`GET /api/export/crashes.csv`)
- Bulk crash deletion with optional filters (`DELETE /api/crashes?container=...&type=...`)
- Notification mute rules per container (`GET/POST/DELETE /api/muted-containers`)
- Acknowledge/unacknowledge crashes without deleting them (`POST /api/crashes/{id}/acknowledge`)
- Incident cards now support acknowledge/unacknowledge actions in the dashboard.
- Filter incidents by acknowledgement state with `GET /api/crashes?acknowledged=true|false`.
- Notification delivery can fan out to Telegram, email, ntfy, and an optional webhook.
- Automatic retention cleanup (`CRASH_RETENTION_DAYS`)
- Health status endpoint (`GET /api/health`)

## API Endpoints

When `API_AUTH_TOKEN` is set, include `X-API-Token: <token>` on all `/api/*` calls except `/api/health`.

- `GET /api/crashes?container=&type=&limit=&offset=&acknowledged=` list crashes (pagination + review filter)
- `GET /api/crashes/{id}` crash details with full logs
- `DELETE /api/crashes/{id}` delete one crash
- `POST /api/crashes/{id}/acknowledge` mark a crash reviewed
- `POST /api/crashes/{id}/unacknowledge` mark a crash active again
- `DELETE /api/crashes?container=&type=` delete filtered crashes
- `GET /api/containers` list live containers
- `GET /api/stats` top-level dashboard stats
- `GET /api/crash-types?limit=` aggregate by crash type
- `GET /api/timeline?hours=` hourly crash counts
- `GET /api/export/crashes.csv?limit=` download CSV
- `GET /api/muted-containers` list active mute rules
- `POST /api/muted-containers?container_name=&minutes=&reason=` add/update mute rule
- `DELETE /api/muted-containers?container_name=` remove mute rule
- `POST /api/test-notify` send test notification to every configured channel
- `POST /api/refresh` run poll loop immediately
- `GET /api/health` app and watcher status

## Stability Defaults

- Overlap-safe scheduler (`max_instances=1`, coalesced runs)
- SQLite WAL mode + busy timeout + retry on lock
- Per-container error isolation in poll loop
- Graceful fallback summaries when AI providers fail
- Clear UI error states when Docker socket is not available
- Docker image includes a native `HEALTHCHECK` against `/api/health`
- The Docker build includes a `test` stage that runs the smoke tests before publish

## Environment Variables

See `.env.example` for full list. Common settings:

- `POLL_INTERVAL_SECONDS=30`
- `MAX_LOG_LINES=200`
- `CRASH_RETENTION_DAYS=30` (`0` disables cleanup)
- `RETENTION_SWEEP_MINUTES=60`
- `DB_TIMEOUT_SECONDS=10`
- `DB_LOCK_RETRY_COUNT=3`
- `DB_LOCK_RETRY_DELAY_SECONDS=0.15`
- `NTFY_URL=https://ntfy.sh`
- `NTFY_TOPIC=`
- `WEBHOOK_URL=`

## Notes for Deployment

- The app requires Docker socket access to inspect sibling containers.
- If Docker access fails, the dashboard remains available and shows the error.
- If notification or AI credentials are missing, monitoring still runs.
- If you want to publish under a different GHCR package name, update the workflow image name in `.github/workflows/docker-publish.yml`.
