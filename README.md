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

### 의존성 관리 (uv 단일화)

**의존성의 단일 출처는 `pyproject.toml` + `uv.lock` 입니다.** 이 레포는
`requirements.txt`를 쓰지 않습니다(Dockerfile·로컬 모두 `uv sync --locked`).

- 의존성 추가/변경: 해당 `pyproject.toml` 수정 → `uv lock` → `uv sync`.
- 손으로 관리하는 `requirements.txt`를 다시 만들지 마세요(과거 uv.lock과 어긋나
  asyncpg 등 누락된 전례 있음).
- uv를 못 쓰는 외부 환경에 의존성 목록이 필요하면 그때 **생성**해서 전달하세요(커밋 X):
  `uv export --package <service> --no-hashes --no-dev --no-emit-workspace`

## Service Boundaries

- `web` calls `main-server`.
- `main-server` owns user-facing APIs, job creation, result lookup, watchlists, and journals.
- `agent-worker` owns data collection, normalization, LLM/RAG analysis, and final signal generation.
- `packages/signal-core` keeps shared data contracts consistent across backend services.
- `packages/data-access` keeps database access patterns reusable without duplicating query logic.
