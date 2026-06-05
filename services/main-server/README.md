# Main Server

User-facing backend API server.

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
