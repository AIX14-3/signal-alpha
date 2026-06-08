# Main Server

User-facing backend API server.

## Local Development

```bash
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
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
