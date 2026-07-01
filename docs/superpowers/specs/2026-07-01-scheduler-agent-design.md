# Scheduler Agent Design

Date: 2026-07-01
Status: Approved for implementation planning

## Goal

운영 배치 안정화를 위해 기존 `run_scheduler_instance.py`를 Scheduler Agent 역할로 명확히 한다. Scheduler Agent는 `collection_schedules` 설정을 읽고, due/manual trigger를 판단하고, worker 내부 endpoint를 호출해 수집 작업을 큐에 넣으며, 실행 결과를 다시 `collection_schedules`에 기록한다.

이 설계의 목표는 DART, Report, Price 트리거를 안정적으로 발화시키는 것이다. 실제 수집, 정규화, 분석, 집계, 발행은 기존 `agent-worker` 큐 핸들러와 큐 드레인 데몬이 계속 담당한다.

## Current Gap

현재 admin API는 schedule target으로 `price`, `dart`, `report`를 허용한다. 그러나 배포용 scheduler의 `_fire()` 흐름은 `dart`와 `price`만 실제 호출한다. 따라서 admin에서 `report` target을 설정해도 `POST /internal/schedules/report/collect`가 발화되지 않는다.

또한 scheduler의 책임이 문서와 코드에서 일부 흐릿하다. 이번 범위에서는 scheduler가 collector/analyzer가 아니라 trigger orchestrator임을 코드와 문서에서 분명히 한다.

## Scope

구현 범위:

- `collection_schedules.targets`에 `report`가 있을 때 `POST /internal/schedules/report/collect`를 호출한다.
- `dart`, `report`, `price` target은 서로 독립적으로 실행한다.
- 한 target 실패가 다른 target 실행을 막지 않는다.
- target별 결과를 `last_detail`에 기록한다.
- 전체 상태는 기존처럼 `ok`, `partial`, `noop` 중 하나로 기록한다.
- scheduler는 `X-Internal-Token`을 포함해 worker 내부 endpoint만 호출한다.
- 큐 소비는 `QUEUE_DRAIN_DAEMON_ENABLED` 기반 큐 드레인 데몬에 맡긴다.
- runbook과 테스트가 현재 동작을 설명하도록 맞춘다.

제외 범위:

- LangGraph 또는 LLM 기반 자율 계획 기능
- 사용자-facing 즉시 분석 버튼
- `web`에서 `agent-worker` 직접 호출
- 수집, 정규화, 분석 로직의 `main-server` 이동
- scheduler가 큐를 직접 drain하는 기본 운영 모드
- Report RAG 또는 멀티 agent debate 구현

## Architecture

Scheduler Agent는 별도 도메인 분석 agent가 아니라 운영 제어 agent다.

구성 요소:

- `main-server`: admin schedule API를 통해 `collection_schedules` 설정과 manual trigger 요청을 기록한다.
- `collection_schedules`: schedule config와 실행 상태를 저장하는 제어 테이블이다.
- `run_scheduler_instance.py`: schedule row를 폴링하고 due/manual trigger를 판단한다.
- `agent-worker` internal API: target별 수집 또는 가격 수집 endpoint를 제공한다.
- `processing_queue`: internal API가 실제 후속 작업을 enqueue한다.
- Queue drain daemon: enqueue된 작업을 체인 순서대로 소비한다.

Scheduler Agent는 `main-server`를 호출하지 않는다. `main-server`도 worker를 직접 호출하지 않고 DB에 제어 상태만 쓴다. 이 구조는 기존 서비스 경계를 유지한다.

## Data Flow

1. Admin이 schedule 설정을 수정하거나 "지금 실행"을 요청한다.
2. `main-server`가 `collection_schedules` row를 갱신한다.
3. Scheduler Agent가 `collection_schedules`를 폴링한다.
4. `enabled`, `run_at_local`, `manual_trigger_requested_at`, `last_run_at`을 기준으로 발화 여부를 판단한다.
5. `targets`에 따라 worker internal endpoint를 호출한다.
6. Worker endpoint가 `processing_queue`에 필요한 작업을 enqueue한다.
7. Queue drain daemon이 수집 이후 정규화, 분석, 집계, 종합, 발행 단계를 처리한다.
8. Scheduler Agent가 target별 실행 요약을 `last_detail`, `last_status`, `last_run_at`, `next_run_at`에 기록한다.

Target mapping:

- `price`: `POST /internal/price/collect`, `price_modes`의 각 mode별 호출
- `dart`: `POST /internal/schedules/dart/collect`, `dart_limit` 사용
- `report`: `POST /internal/schedules/report/collect`, deadline MVP 기본값 사용

Report target은 별도 DB 컬럼이 아직 없으므로 기존 request default와 명시 기본값을 사용한다.

- `limit`: 100
- `days_back`: 7
- `max_pages`: 20
- `priority`: `batch`

## Error Handling

각 target 호출은 독립 try/catch로 감싼다. 실패한 target은 `last_detail`에 `"error: ..."` 형식으로 기록하고, 다음 target 실행을 계속한다.

전체 상태 기준:

- `noop`: 실행할 target이 없거나 결과가 비어 있음
- `ok`: 모든 target이 오류 없이 요약됨
- `partial`: 하나 이상의 target에 오류가 있음

Scheduler Agent 자체가 DB 연결, 환경 변수, internal token 같은 필수 조건을 만족하지 못하면 해당 주기는 실패 로그를 남기고 다음 폴링 주기로 넘어간다. `--once` 실행에서는 한 번 평가 후 종료한다.

## Testing

가장 좁은 테스트는 `services/agent-worker`에 둔다.

- `_fire()`가 `report` target을 보면 `/internal/schedules/report/collect`를 호출하는지 검증한다.
- `dart`, `report`, `price`가 함께 있을 때 target별 summary가 남는지 검증한다.
- 하나의 target이 HTTP 오류를 내도 다른 target 호출이 계속되는지 검증한다.
- `INTERNAL_API_TOKEN` 없을 때 기존처럼 오류를 내는지 유지한다.
- runbook 또는 runtime config 테스트가 stale queue drain 설명을 만들지 않도록 갱신한다.

검증 명령:

```powershell
cd services/agent-worker
uv run pytest tests/test_scheduler_internal_auth.py -q
```

필요하면 scheduler 관련 새 테스트 파일을 추가하고 같은 범위에서 실행한다.

## Acceptance Criteria

- Admin schedule target에 `report`가 포함되면 실제 Report collection enqueue endpoint가 호출된다.
- Scheduler Agent는 수집/분석 로직을 직접 수행하지 않는다.
- `web -> main-server -> DB schedule config -> scheduler -> agent-worker internal API -> processing_queue` 경계가 유지된다.
- Report target 실패가 Price 또는 DART trigger를 막지 않는다.
- 실행 결과가 admin에서 확인 가능한 `last_detail`에 남는다.
- 문서가 queue drain daemon이 기본 소비자라는 현재 구현과 일치한다.
