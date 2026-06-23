# Main Server

User-facing backend API server.

## Local Development

```bash
uv sync --package signal-alpha-main-server --extra dev
uv run --package signal-alpha-main-server uvicorn app.main:app --reload
```

Health check:

```text
GET /health
```

Expected response:

```json
{
  "status": "ok",
  "service": "main-server",
  "version": "0.1.0"
}
```

Implemented user-facing endpoints:

```text
POST /api/auth/signup
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
GET  /api/users/me

GET    /api/stocks/search?query=
GET    /api/watchlists
POST   /api/watchlists
DELETE /api/watchlists/{stock_code}

GET /api/dashboard

GET /signals/{ticker}
GET /api/signals/{signal_id}
POST /api/signals/{signal_id}/read

GET    /api/journals
POST   /api/journals
GET    /api/journals/{journal_id}
PATCH  /api/journals/{journal_id}
DELETE /api/journals/{journal_id}
```

## Responsibilities

- Expose frontend APIs.
- Manage watchlists, journals, and signal result lookup.
- Create and track analysis jobs.
- Trigger the agent worker.
- Store or retrieve final results for the dashboard.

## Does Not Own

- External source crawling.
- LLM prompt execution.
- RAG pipeline internals.
