# Signal Alpha

Signal Alpha is a monorepo for a frontend, a main API server, and an agent worker that separates data collection from LLM-based analysis.

## Repository Structure

```text
web/                    # Frontend application
services/
  main-server/          # User-facing API server
  agent-worker/         # Data collection and LLM analysis worker
packages/
  signal-core/          # Shared schemas, enums, and domain types
  data-access/          # Shared database access helpers
docs/                   # Project documentation
```

## Service Boundaries

- `web` calls `main-server`.
- `main-server` owns user-facing APIs, job creation, result lookup, watchlists, and journals.
- `agent-worker` owns data collection, normalization, LLM/RAG analysis, and final signal generation.
- `packages/signal-core` keeps shared data contracts consistent across backend services.
- `packages/data-access` keeps database access patterns reusable without duplicating query logic.
