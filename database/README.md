# Signal Alpha Database

Signal Alpha 데이터베이스는 DART 공시, 증권사 리포트, 채용공고, 특허, DataLab 검색 트렌드, 주가 데이터를 수집하고 정규화한 뒤, 여러 데이터 소스가 같은 방향의 투자 시그널을 보이는지 분석하기 위한 PostgreSQL 기반 저장소입니다.

> ## ⚠️ 소스 오브 트루스 선언
>
> **DB 스키마의 유일한 기준은 `database/migrations/` 입니다.**
>
> - 다른 문서(기획서, `docs/project-context.md`의 스키마 초안 등)와 충돌하면 **마이그레이션이 항상 우선**합니다.
> - 코드에서 `CREATE TABLE`을 실행하는 것은 **금지**입니다 (과거 `setup_db.py`가 이 규칙을 어겨 스키마가 이중화됐고, 해당 파일은 삭제됐습니다).
> - 스키마를 바꾸려면 반드시 **새 마이그레이션 파일**을 추가하세요 (§4 절차).

## 1. 마이그레이션 러너 사용법

마이그레이션은 `database/migrate.py`가 적용하고, 적용 이력은 DB의 `schema_migrations` 테이블(원장)에 기록됩니다. psql 수동 실행은 더 이상 사용하지 않습니다.

```bash
# Docker (권장) — postgres 기동 후 일회성 적용
docker compose up -d postgres
docker compose run --rm db-migrate apply --seeds

# 로컬 (Windows/macOS) — 루트에서
uv run python database/migrate.py status         # 적용 현황 확인
uv run python database/migrate.py apply          # 미적용분만 순서대로 적용
uv run python database/migrate.py apply --seeds  # 적용 후 시드까지
uv run python database/migrate.py apply --dry-run
```

- DATABASE_URL은 `--database-url` 옵션 > 환경변수 > 루트 `.env` 순으로 찾습니다.
- 파일당 한 트랜잭션으로 실행되며, 실패 시 해당 파일 전체가 롤백되고 중단됩니다.
- **적용된 마이그레이션 파일은 절대 수정 금지.** 러너가 sha256 checksum으로 검증하여 수정 시 에러를 냅니다. 변경은 항상 `python database/migrate.py new "..."`로 만든 새 타임스탬프 파일에 추가하세요.

드리프트 검사 (내 DB가 마이그레이션과 일치하는지):

```bash
uv run python database/tools/check_schema.py
```

불일치 시 차이가 표로 출력되고 exit 1. 개발 DB라면 재생성이 가장 깔끔합니다:

```bash
docker compose down -v && docker compose up -d postgres
docker compose run --rm db-migrate apply --seeds
```

## 2. 전체 Zone 구조 (테이블 인벤토리)

> **베이스라인 구조 (#531 2-인스턴스 재베이스라인):** 스키마는 더 이상 단일 `001_baseline.sql`
> 한 파일이 아닙니다. 수집(워커) DB / 백엔드(서비스) DB **물리 분리**에 맞춰 타깃별 베이스라인
> `0001_infra_roles` ~ `0007_backend_grants` 로 나뉘고, 이후 변경은 타임스탬프 증분 마이그입니다.
> 구 단일 `001_baseline.sql`(및 001~029 레거시)는 `migrations/archive/` 로 이동했으며 **적용 대상이
> 아닙니다**(러너는 `migrations/*.sql` 만 글롭, archive 비재귀 무시). 타깃별 베이스라인 구성·테이블→
> DB 매핑의 단일 출처는 [`docs/migration_seed_targets.md`](./docs/migration_seed_targets.md) 입니다.

아래 Zone 표는 **테이블 역할 인벤토리**입니다(어느 DB 에 사는지는 위 문서의 target 매핑 참조).
**새 테이블을 만들기 전에 이 표에서 역할이 겹치는 테이블이 없는지 먼저 확인하세요.**

| Zone | 테이블 |
| --- | --- |
| A Market | `stocks`, `ohlcv_data`, `fundamentals`, `price_snapshots`, `short_selling_trend`, `credit_trade_trend`, `securities_lending_trend` |
| C Collection 핵심 | `collector_runs`, `raw_documents`, `dart_raw_details`, `report_raw_details`, `hiring_raw_details`, `patent_raw_details` |
| C Collection DART | `dart_corp_codes`, `dart_collection_states` |
| C Collection DataLab | `datalab_categories`, `datalab_category_stocks`, `datalab_category_keywords`, `datalab_raw_documents`, `datalab_raw_details` |
| C Collection Hiring | `hiring_baseline`, `hiring_signals`, `hiring_sources`, `hiring_job_functions`, `hiring_job_function_stocks`, `hiring_quarantine` (+ `hiring_crawler_type` ENUM) |
| D Processing | `processing_queue`, `dead_letter`, `source_documents`, `signal_events`, `signal_metrics`, `validation_logs` |
| B User 기본 | `users`, `subscription_plans` |
| E Analysis | `analysis_requests`, `analysis_results`, `quant_scores`, `ta_scores`, `ai_scores`, `agent_results`, `xgb_model_versions`, `ml_scores`, `final_signals`, `score_history`, `backtest_results` |
| E Agent 메모리/비활성 임베딩 스키마 | `signal_episodes`(에피소드 메모리), `report_chunks`(비활성 Report RAG 잔존 스키마; 현재 런타임 미사용) |
| F User 확장 | `signal_subscriptions`, `watchlists`, `signal_journals`, `signal_journal_outcomes`, `signal_journal_chart_prices`, `user_signal_reads`, `user_sessions`, `social_accounts`, `portone_verifications`, `terms_agreements` |
| G Admin | `admin_accounts`, `admin_sessions` |
| H Guard (지정학 Kill-Switch) | `guard_site_status`, `guard_news_events`, `guard_recommendations`, `guard_status_audit` (backend 소유) |
| I 커뮤니티 게시판 | `community_posts`, `community_comments`, `community_reactions`, `community_post_views`, `community_reports`, `community_post_rankings` (backend 소유, 유저 콘텐츠 공개 공유 레이어) |
| 트리거 | (트리거 함수 2종 + updated_at 트리거 일괄 부착) |
| ~~Legacy~~ | ~~`report_raw`, `report_signal`~~ ← **제거됨** (`20260630_1200…`, §7) |

이 외에 러너가 각 DB 에 자동 생성하는 `schema_migrations` 원장이 있습니다.

이후 스키마 변경은 베이스라인 파일을 수정하지 않고 `YYYYMMDD_HHMM_*.sql` 증분 마이그레이션으로 추가합니다 ([`docs/migration_rules.md`](./docs/migration_rules.md) §3). 표 베이스라인(`0002`/`0003`/`0004`)은 손으로 고치지 말고 `rebaseline.py` 로 재생성합니다.

DataLab은 종목이 아닌 **카테고리 단위**로 수집합니다: 원본은 `datalab_raw_documents`/`datalab_raw_details`(category_id 기반)에 저장하고, `datalab_category_stocks` 매핑으로 종목을 해석합니다. `processing_queue.stock_id`가 NULL 허용인 이유도 이것입니다.

전체 ERD: [`database/erd/signal_alpha_core_erd.md`](./erd/signal_alpha_core_erd.md)
테이블별 역할 한 줄 설명: [`database/docs/table_descriptions.md`](./docs/table_descriptions.md)
설계 규칙 문서: [`database/docs/`](./docs/) (db_design_summary, run_key_rule, source_hash_rule, table_responsibility, table_descriptions)

## 3. 컨벤션

| 항목 | 규칙 |
| --- | --- |
| PK | `id BIGSERIAL PRIMARY KEY` (detail 테이블은 `raw_document_id BIGINT PRIMARY KEY`) |
| 시간 컬럼 | `TIMESTAMPTZ` 사용 (`TIMESTAMP` 금지). `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` 기본 포함 |
| updated_at | 컬럼이 있으면 012 컨벤션대로 `trg_<table>_updated_at` 트리거를 함께 추가 |
| 제약/인덱스 명명 | `uq_` (unique), `idx_` (index), `trg_` (trigger), `chk_` (check) 접두 |
| `IF NOT EXISTS` | **신규 테이블 정의(plain `CREATE TABLE`)에는 쓰지 않는다** — 적용 여부는 원장이 관리하고, 과거 스키마 충돌(report_chunks 이중 정의)을 은폐한 주범. **단, 증분 변경의 멱등 가드는 허용·권장**: `ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS … ADD`, `CREATE OR REPLACE VIEW`, `CREATE SCHEMA IF NOT EXISTS`, 롤/grant 의 `IF [NOT] EXISTS` 가드(0001/0006/0007 처럼). 재적용·2-DB 부분적용 안전성을 위함 |
| FK ON DELETE | raw detail 테이블 = `CASCADE` (원본 삭제 시 상세 동반 삭제), 분석/이력 테이블 = NO ACTION(기본, 이력 보존) |
| 데이터 시드 | 마이그레이션에 넣지 않고 `seeds/NNN_*.sql`로 분리. 시드는 `ON CONFLICT` 기반 재실행 안전(idempotent) 필수 |
| ENUM성 컬럼 | `VARCHAR + CHECK` 사용 (PostgreSQL ENUM 타입 미사용) |

## 4. 새 테이블/컬럼 추가 절차 ← 스키마 변경 시 필독

> 협업 규칙·PR 체크리스트 전문은 [`docs/migration_rules.md`](./docs/migration_rules.md)에 있습니다. 적용된 파일은 freeze이므로 수정하지 말고, 새 변경은 **타임스탬프 파일**(`python database/migrate.py new "..."`)로 추가합니다(정수 순번 `NNN_`은 폐기).

1. **§2 인벤토리에서 중복 확인** — 같은 역할의 테이블이 이미 있으면 재사용/확장.
2. **타임스탬프 파일** 생성: `python database/migrate.py new "짧은설명"` → `YYYYMMDD_HHMM_짧은설명.sql`.
   - 정수 순번(`NNN_`)은 폐기(브랜치 병렬 충돌). 레거시 `001~023`은 freeze, 새 건 타임스탬프로.
   - 논리적 변경 1건 = 파일 1개 (테이블 신설 + 무관한 ALTER를 한 파일에 섞지 않기)
3. §3 컨벤션 준수 (plain `CREATE TABLE`, TIMESTAMPTZ, 명명 규칙, updated_at 트리거).
4. 로컬 검증:
   ```bash
   uv run python database/migrate.py apply
   uv run python database/tools/check_schema.py
   ```
5. **문서 동기화 (필수):** 이 README §2 표 + `database/erd/signal_alpha_core_erd.md`(ERD) + `database/docs/table_descriptions.md`(테이블 역할 한 줄 설명)에 새 테이블 반영.
6. PR 체크리스트의 "DB 변경" 섹션 체크.
7. **번호 충돌 없음:** 타임스탬프 접두사라 동시 PR끼리 파일명이 겹치지 않아 리넘버가 불필요합니다(`NNN_` 순번을 폐기한 이유). 정확히 같은 분·같은 slug로만 드물게 겹치면 한쪽 파일명을 1분 조정.

## 5. Collector별 저장 흐름

모든 Collector는 실행 시작 시 `collector_runs`에 실행 로그를 만들고, 수집 원본 공통 메타데이터를 `raw_documents`에 저장합니다. 이후 source별 detail 테이블에 원본 상세 데이터를 저장하고, 후속 처리를 위해 `processing_queue`에 정규화 작업을 등록합니다.

| Collector | 저장 흐름 |
| --- | --- |
| DART Collector | `collector_runs` -> `raw_documents` -> `dart_raw_details` -> `processing_queue` |
| Report Collector | `collector_runs` -> `raw_documents` -> `report_raw_details` -> `processing_queue` |
| Hiring Collector | `collector_runs` -> `raw_documents` -> `hiring_raw_details` -> `processing_queue` (계절성 기준선은 `hiring_baseline`) |
| Patent Collector | `collector_runs` -> `raw_documents` -> `patent_raw_details` -> `processing_queue` |
| DataLab Collector | `collector_runs` -> `datalab_raw_documents` -> `datalab_raw_details` -> `processing_queue` (stock_id=NULL, 카테고리 기반) |
| Price Collector | `collector_runs` -> `price_snapshots` + `ohlcv_data` (시세 전용 — `raw_documents`/`processing_queue` 미사용) |

Collector는 LLM을 호출하지 않습니다. 원본 데이터 저장, 중복 방지용 `source_hash` 생성, detail 저장, 처리 큐 등록까지만 담당합니다.

## 6. Agent별 조회/저장 흐름

Normalizer는 raw 계층을 직접 분석하지 않고 정규화 계층을 생성합니다.

```text
raw_documents + detail tables
-> source_documents
-> signal_events
-> signal_metrics
-> validation_logs
```

### source_documents 앵커 (raw 추적)

`source_documents`는 정규화 행이 어떤 raw에서 나왔는지 두 가지 방식으로 앵커합니다 (`004_datalab_source_anchor.sql`):

| 소스 | 앵커 | 카디널리티 |
| --- | --- | --- |
| DART / REPORT / HIRING / PATENT | `raw_document_id` → `raw_documents(id, stock_id)` (복합 FK) | raw 1건 = 종목 1개 (1:1) |
| DataLab (및 향후 비-`raw_documents` 소스) | `external_ref_type` + `external_ref_id` (범용 외부 앵커) | 관측 1건 → 종목 N개 (1:N fan-out) |

- `chk_source_doc_anchor`: 두 앵커 방식 중 **정확히 하나만** 채워집니다.
- DataLab은 `external_ref_type='datalab_raw_documents'`, `external_ref_id=datalab_raw_documents.id`로 앵커하고, `datalab_category_stocks` 매핑으로 종목별 행이 fan-out 됩니다 (`uq_source_doc_external` 부분 유니크가 멱등 보장).
- **무결성은 현재 soft 참조(D-soft)** — `external_ref_*`는 선언적 FK가 아니며 존재 보장은 Normalize 핸들러 책임입니다. mock 단계에서 데이터 소스가 자주 추가/삭제/수정되어도 `datalab_raw_documents`를 자유롭게 재생성할 수 있게 한 의도적 선택입니다.
- **운영 승격 시 업그레이드 경로(D-trigger):** 소스 셋이 안정화되면 다음 마이그레이션에서 `external_ref_type`별 존재를 검증하는 `BEFORE INSERT OR UPDATE` 트리거로 강화합니다(새 소스 = CASE 분기 한 줄, 컬럼/제약 변경 없음). 전체 트리거 예시는 `004_datalab_source_anchor.sql` 헤더 주석에 있습니다.

Agent는 정규화된 `source_documents`, `signal_events`, `signal_metrics`를 조회해 분석합니다. 분석 결과는 `analysis_results`에 대표 단위로 저장하고, 방식별 결과는 `agent_results`에 저장합니다.

```text
source_documents / signal_events / signal_metrics
-> analysis_results
-> agent_results
-> ml_scores
-> final_signals
-> score_history
```

Frontend는 원칙적으로 `final_signals`를 중심으로 조회합니다.

`final_signals.is_current`는 `stock_id + signal_date + run_key` 기준 현재 대표 시그널을 의미하며, 같은 조합 안에서 `is_current = TRUE`인 row는 1개만 존재합니다 (트리거 `set_final_signal_current`가 보장).

| 실행 상황 | run_key |
| --- | --- |
| 오전 리포트 반영 정기 분석 | `AM` |
| 오후 리포트 반영 정기 분석 | `PM` |
| 야간 배치 분석 | `BATCH_NIGHT` |
| DART 고임팩트 즉시 분석 | `IMMEDIATE` |
| 수동 재분석 | `MANUAL` |

`signal_episodes`는 에피소드 메모리용 pgvector 스키마입니다. `report_chunks`는 과거 Report RAG 계획에서 추가된 잔존 스키마지만, 현재 Report 런타임에서는 `report_chunks`를 적재하거나 조회하지 않습니다. 신규 Report 개발은 `raw_documents -> report_raw_details -> processing_queue` 이후 정규화/분석 테이블과 `report_valuation_facts`를 기준으로 합니다.

## 7. Legacy 테이블 (제거됨)

`report_raw` / `report_signal`은 report RAG MVP가 마이그레이션 체계 밖(`setup_db.py`)에서 만들어 쓰던 테이블입니다. report 런타임 코드가 canonical 경로(`raw_documents` -> `report_raw_details`)로 완전히 이전돼 더 이상 참조하지 않음을 확인하고(코드 참조 0, 2026-06-30), **`20260630_1200_drop_legacy_report_raw_signal.sql`(target: collection)로 DROP** 했습니다.

- 리포트 데이터는 항상 `raw_documents` -> `report_raw_details` 경로를 사용하세요.
- 베이스라인 `0003_collection_baseline.sql`에는 생성 구문이 이력으로 남아 있으나 위 마이그가 적용 직후 제거합니다(그린필드: 생성→DROP). 다음 `rebaseline.py` 재생성 시 베이스라인에서도 사라집니다.

## 8. 주의사항

- Signal Alpha가 제공하는 시그널은 AI Agent의 데이터 분석 결과이며 투자 권유가 아닙니다. 투자 판단과 손실 책임은 사용자 본인에게 있습니다.
- 수치 데이터는 LLM이 생성하지 않습니다. 점수, 지표, 가격, 변화율, 재무 수치 등 정량 데이터는 반드시 DB에 저장된 값만 사용합니다.
- PostgreSQL 배열 컬럼(`processing_queue.source_*_ids`, `analysis_results.source_signal_event_ids`, `agent_results.source_signal_event_ids`)은 원소별 FK 무결성을 강제하지 못합니다. MVP에서는 배열을 유지하고 `validation_logs`로 source trace 검증을 기록하며, 안정화 후 매핑 테이블로 분리합니다.
- `source_hash`, `event_hash`, unique constraint, partial unique index로 중복 저장을 방지합니다.
