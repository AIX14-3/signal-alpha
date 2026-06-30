---
name: run-pipeline
description: signal-alpha agent-worker 파이프라인 로컬 기동·검증. 워커/스케줄러/드레인 데몬 가동과 COLLECT→NORMALIZE→ANALYZE→AGGREGATE→SYNTHESIZE→PUBLISH end-to-end 확인. "워커 실행", "파이프라인 돌려", "드레인", "스모크" 시 사용.
allowed-tools: Bash, Read, Grep, Glob
---

# 파이프라인 기동 / 검증

작업 디렉터리: `services/agent-worker`.

## 인프라
- DB+마이그: `docker compose up -d postgres db-migrate` (레포 루트)
- 워커(API+데몬): `docker compose up -d --build agent-worker` (QUEUE_DRAIN_DAEMON_ENABLED=true)
- 로컬 API: `uv run uvicorn app.main:app --reload --port 8011`

## 단계별 로컬 실행
- 수집(+corp_code sync): `uv run python run_collectors.py`
- 정규화(DART): `uv run python run_normalizers.py`
- 분석(7영역): `uv run python run_analyzers.py`
- 통합 인스턴스(가격 수집+hiring+드레인 데몬): `uv run python run_scheduler_instance.py [--once]`
- 외부 스케줄러: `uv run python run_scheduler_instance.py --interval-seconds 1800 --base-url http://localhost:8011`

## 드레인 / E2E
- 단발 드레인: `uv run python run_worker_drain.py`
- 연속 드레인(로컬): `uv run python run_worker_drain.py --watch --interval-seconds 5`
- 합성 스모크: `uv run python smoke_synthesis.py`

## 검증 포인트
- `processing_queue`가 비워지는지(`/db-check` 5번 쿼리).
- final_signals 생성 확인. 발행까지 보려면 `BACKEND_DATABASE_URL` 설정 후 `/publish-report`.
