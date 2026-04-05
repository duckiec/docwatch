# DocWatch

DocWatch is a self-hosted Docker crash analyzer.
It monitors container state changes, records crash events, summarizes likely causes with AI, and sends notifications.

## Run

1. Copy `.env.example` to `.env` and set any keys you want to use.
2. Start:

```bash
docker-compose up --build
```

3. Open `http://localhost:8080`.

## Notes

- If AI credentials are missing or API calls fail, DocWatch stores a fallback summary and keeps running.
- If Docker socket access is unavailable, the dashboard shows a clear error instead of crashing.
- Crash alerts dedupe via tracked `container_state` restart counts and status transitions.
- Watch polling is overlap-safe (`max_instances=1`, coalesced runs) to avoid duplicate work when Docker is slow.
- SQLite runs in WAL mode with busy timeout and retry-on-lock logic for better stability under concurrent API + watcher access.
- The dashboard now handles transient API failures gracefully and avoids unsafe HTML injection by rendering crash content with text nodes.

## Health Check

- `GET /api/health` returns service state, watcher status, current UTC time, and Docker connectivity status.

## Optional Stability Tuning

You can add these optional vars in `.env` to tune DB lock handling:

- `DB_TIMEOUT_SECONDS=10`
- `DB_LOCK_RETRY_COUNT=3`
- `DB_LOCK_RETRY_DELAY_SECONDS=0.15`
