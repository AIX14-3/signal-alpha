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

## Python Development

This repository is configured as a `uv` workspace. The workspace members are the
Python services under `services/` and shared packages under `packages/`.

Install `uv`, then sync the workspace from the repository root:

```powershell
uv sync --all-packages --group dev
```

Run Python commands through `uv run` so they use the locked workspace
environment. Run service commands from each service directory so the local
`app/` module resolves correctly:

```powershell
cd services/main-server
uv run pytest

cd ../agent-worker
uv run pytest

cd ../../packages/data-access
uv run pytest

cd ../signal-core
uv run pytest
```

Service examples:

```powershell
cd services/main-server
uv run uvicorn app.main:app --reload

cd ../agent-worker
uv run uvicorn app.main:app --reload --port 8011
```

## Service Boundaries

- `web` calls `main-server`.
- `main-server` owns user-facing APIs, job creation, result lookup, watchlists, and journals.
- `agent-worker` owns data collection, normalization, LLM/RAG analysis, and final signal generation.
- `packages/signal-core` keeps shared data contracts consistent across backend services.
- `packages/data-access` keeps database access patterns reusable without duplicating query logic.
