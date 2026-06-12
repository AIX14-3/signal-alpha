# AGENTS.md

이 디렉터리는 PostgreSQL + pgvector 스키마, migration, seed, ERD, DB 거버넌스 문서를 담당합니다.

## 스키마 기준

`database/migrations/`가 유일한 스키마 기준입니다. 다른 문서가 migration과 다르면 migration을 우선합니다.

## Migration 규칙

- 이미 적용된 migration은 절대 수정하지 마세요.
- 스키마 변경은 새 번호의 migration으로 추가하세요.
- 하나의 논리적 스키마 변경은 하나의 migration 파일에 담으세요.
- 애플리케이션 코드에서 테이블을 만들지 마세요.
- 거버넌스 문서를 명시적으로 갱신하지 않는 한 `IF NOT EXISTS`를 사용하지 마세요.
- plain `TIMESTAMP`가 아니라 `TIMESTAMPTZ`를 사용하세요.
- enum 성격 컬럼은 `VARCHAR + CHECK`를 사용하세요.
- seed는 migration과 분리하고 재실행 가능하게 만드세요.

## 테이블 책임

canonical 수집 흐름:

- DART: `collector_runs -> raw_documents -> dart_raw_details -> processing_queue`
- Report: `collector_runs -> raw_documents -> report_raw_details -> report_chunks -> processing_queue`
- Hiring: `collector_runs -> raw_documents -> hiring_raw_details -> processing_queue`
- Patent: `collector_runs -> raw_documents -> patent_raw_details -> processing_queue`
- DataLab: `collector_runs -> datalab_raw_documents -> datalab_raw_details -> processing_queue(stock_id=NULL)`
- Price: `collector_runs -> price_snapshots + ohlcv_data`

canonical 분석 흐름:

```text
source_documents / signal_events / signal_metrics
-> analysis_results
-> agent_results
-> final_signals
```

## Legacy

`report_raw`, `report_signal`은 legacy Report MVP 테이블입니다. 기존 코드 때문에 임시로 유지 중이며 신규 코드에서 사용하면 안 됩니다.

## 검증

저장소 루트에서 실행합니다.

```powershell
uv run python database/migrate.py status
uv run python database/migrate.py apply
uv run python database/tools/check_schema.py
```

스키마를 변경했다면 아래 문서도 갱신하세요.

- `database/README.md`
- `database/erd/signal_alpha_core_erd.md`
- 관련 table responsibility 문서
