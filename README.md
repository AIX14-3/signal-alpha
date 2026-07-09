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
docs/                   # Project documentation (index: docs/README.md)
```

See [`docs/README.md`](docs/README.md) for the documentation index (overview, architecture,
data pipeline, development guide, glossary, and specs).

## Quick Start (Docker Compose)

로컬 전체 스택(postgres · agent-worker · main-server · web)을 한 번에 띄운다:

```bash
cp .env.example .env
docker compose up -d --build
# 마이그레이션 적용(최초 1회 또는 스키마 변경 시)
docker compose run --rm db-migrate apply --seeds
```

기동 후 프론트는 http://localhost:3000, API 는 http://localhost:8000 이다.

### 로컬 함정 (막히면 여기부터)

- **web `node_modules` 명명볼륨 stale** — 프론트 의존성이 추가되면(예: `lucide-react`)
  `Module not found` 로 빌드가 깨진다(Docker 명명볼륨은 최초 생성 시에만 채워짐, 이미지 재빌드로도 안 들어옴).
  해소: `docker compose exec -T web npm ci && docker compose restart web`
  또는 `docker volume rm signal-alpha_web-node-modules` 후 재기동.
- **포트 3000 Windows 예약(winnat)** — 동적 예약범위에 걸리면 web 바인딩 실패.
  관리자 PowerShell 에서 `net stop winnat && net start winnat`, 또는 `WEB_PORT` 를 다른 값으로.
- **docker `credsStore` 접근거부** — `~/.docker/config.json` 의 `"credsStore": "desktop"` 이
  퍼블릭 이미지 빌드에서 크리덴셜 헬퍼 실패를 유발할 수 있다. 빌드 전 그 줄을 임시 제거.
- **로컬 수집(픽스처 계산)** — `run_collectors.py` 는 기본 SSL `require`(prod 전제)라 로컬 평문
  postgres 에선 실패한다. `.env` 의 `COLLECTOR_DB_SSL=disable` 로 끄고 실행한다.

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

## Frontend Development

The frontend uses the lockfile under `web/package-lock.json`. Use `npm ci`
for a reproducible install, then run the same verification contract used by CI:

```powershell
cd web
npm ci
npm run verify
```

`npm run verify` runs the TypeScript typecheck, production build, source-level
frontend test suite, and render smoke test in sequence.

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
