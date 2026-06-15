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

### requirements.txt (uv 미사용 환경/배포용)

**의존성의 단일 출처(source of truth)는 `uv.lock`입니다.** uv를 쓰지 않는 배포
환경/도구를 위해 서비스별 `requirements.txt`를 `uv.lock`에서 **파생 생성**해 둡니다
(`pip install -r`로 설치 가능). 직접 수정하지 말고, 의존성이 바뀌면 아래로 재생성합니다.

```powershell
uv export --package signal-alpha-agent-worker --no-hashes --no-dev --no-emit-workspace -o services/agent-worker/requirements.txt
uv export --package signal-alpha-main-server  --no-hashes --no-dev --no-emit-workspace -o services/main-server/requirements.txt
```

- `--no-dev`: 런타임 의존성만 (테스트/린트 제외)
- `--no-emit-workspace`: 워크스페이스 멤버(PyPI에 없는 내부 패키지)는 제외, 그 전이 의존성만 포함
- 각 파일 상단 주석에 생성 명령이 기록됩니다.

## Service Boundaries

- `web` calls `main-server`.
- `main-server` owns user-facing APIs, job creation, result lookup, watchlists, and journals.
- `agent-worker` owns data collection, normalization, LLM/RAG analysis, and final signal generation.
- `packages/signal-core` keeps shared data contracts consistent across backend services.
- `packages/data-access` keeps database access patterns reusable without duplicating query logic.
