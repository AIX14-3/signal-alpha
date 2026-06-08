# Signal Alpha Database

Signal Alpha 데이터베이스는 DART 공시, 증권사 리포트, 채용공고, 특허, DataLab 검색 트렌드, 주가 데이터를 수집하고 정규화한 뒤, 여러 데이터 소스가 같은 방향의 투자 시그널을 보이는지 분석하기 위한 PostgreSQL 기반 저장소입니다.

## 1. DB 설계 목적

이 DB 설계의 목적은 다음과 같습니다.

- 수집 원본 데이터를 `raw_documents`와 source별 detail 테이블에 보존
- Collector, Normalizer, Agent, ML Scorer의 역할과 저장 책임 분리
- 모든 원천 데이터를 `source_documents`, `signal_events`, `signal_metrics` 구조로 정규화
- 최종 시그널에서 분석 결과, 시그널 이벤트, 근거 문서, 원본 데이터까지 역추적 가능
- 오전, 오후, 즉시, 수동 분석 실행 단위를 `run_key`로 분리
- 재분석, 모델 변경, 점수 변화 이력을 관리
- 무료/유료 서비스 확장을 위한 구독, 열람, 관심종목, 저널 구조 제공
- MVP Report RAG 구현을 위해 `pgvector`, `report_chunks`, embedding index를 필수 구조로 반영

## 2. 전체 Zone 구조

| Zone | 영역 | 주요 테이블 |
| --- | --- | --- |
| Zone A | Market | `stocks`, `ohlcv_data`, `fundamentals`, `sectors`, `sector_ohlcv` |
| Zone B | User / Billing 기본 | `users`, `subscription_plans` |
| Zone C | Collection / Raw | `collector_runs`, `raw_documents`, `dart_raw_details`, `report_raw_details`, `hiring_raw_details`, `patent_raw_details`, `datalab_raw_details`, `report_chunks` |
| Zone D | Processing / Normalization | `processing_queue`, `source_documents`, `signal_events`, `signal_metrics`, `validation_logs` |
| Zone E | Analysis | `analysis_requests`, `analysis_results`, `quant_scores`, `ta_scores`, `ai_scores`, `agent_results`, `xgb_model_versions`, `ml_scores`, `final_signals`, `score_history`, `backtest_results` |
| Zone F | User / Billing 확장 | `signal_subscriptions`, `watchlists`, `signal_journals`, `user_signal_reads`, `social_accounts`, `portone_verifications`, `terms_agreements` |
| Zone G | Admin | `admin_accounts`, `admin_sessions` |

## 3. MVP 필수 테이블

MVP에서 우선 생성하고 사용하는 필수 테이블은 다음과 같습니다.

- Market: `stocks`, `ohlcv_data`
- User / Billing 기본: `users`, `subscription_plans`
- Collection / Raw: `collector_runs`, `raw_documents`, `dart_raw_details`, `report_raw_details`, `report_chunks`, `hiring_raw_details`, `patent_raw_details`, `datalab_raw_details`
- Processing / Normalization: `processing_queue`, `source_documents`, `signal_events`, `signal_metrics`
- Analysis: `analysis_requests`, `analysis_results`, `agent_results`, `final_signals`
- Trigger: `update_updated_at()`, `set_final_signal_current()`

## 4. 확장 테이블

MVP 이후 안정성, ML, 유료화, 운영 기능을 위해 다음 테이블을 확장 사용합니다.

- 안정성: `validation_logs`
- 재무/ML/백테스팅: `fundamentals`, `quant_scores`, `ta_scores`, `ai_scores`, `xgb_model_versions`, `ml_scores`, `score_history`, `backtest_results`
- 회원/유료화: `signal_subscriptions`, `watchlists`, `signal_journals`, `user_signal_reads`
- 실서비스 확장: `social_accounts`, `portone_verifications`, `terms_agreements`
- 운영 관리: `admin_accounts`, `admin_sessions`

초기 개발에서는 모든 테이블을 서비스 코드에서 바로 사용하지 않습니다. 우선 사용 흐름은 다음 핵심 경로를 기준으로 합니다.

```text
stocks
-> raw_documents
-> report_raw_details
-> report_chunks
-> source_documents
-> signal_events
-> signal_metrics
-> analysis_results
-> agent_results
-> final_signals
```

`quant_scores`, `ta_scores`, `ai_scores`, `xgb_model_versions`, `ml_scores`, `score_history`, `backtest_results`, `social_accounts`, `portone_verifications`, `terms_agreements`, `admin_*`는 확장 단계에서 사용합니다.

## 5. Migration 실행 순서

PostgreSQL 기준이며, MVP Report RAG 구현을 위해 `pgvector`를 필수로 사용합니다. 파일 번호 순서대로 실행해야 외래키 참조 순서가 깨지지 않습니다.

```bash
psql -d signal_alpha -f migrations/001_extensions.sql
psql -d signal_alpha -f migrations/002_market.sql
psql -d signal_alpha -f migrations/003_users_billing_base.sql
psql -d signal_alpha -f migrations/004_collection_raw.sql
psql -d signal_alpha -f migrations/005_processing_normalization.sql
psql -d signal_alpha -f migrations/006_analysis.sql
psql -d signal_alpha -f migrations/007_user_billing_extend.sql
psql -d signal_alpha -f migrations/008_admin.sql
psql -d signal_alpha -f migrations/009_triggers.sql
psql -d signal_alpha -f migrations/010_report_chunk_index.sql
```

`010_report_chunk_index.sql`은 Report RAG 검색을 위한 embedding index를 생성합니다. 개발 초기에는 데이터가 적어도 index 생성은 가능하지만, 실제 검색 품질은 청크 데이터가 쌓인 뒤 더 안정적입니다.

필수 migration을 한 번에 실행할 경우:

```bash
for file in migrations/*.sql; do
  psql -d signal_alpha -f "$file"
done
```

Windows PowerShell에서 실행할 경우:

```powershell
Get-ChildItem .\migrations\*.sql | Sort-Object Name | ForEach-Object {
    psql -d signal_alpha -f $_.FullName
}
```

## 6. Seed 실행 방법

seed 파일은 `seeds/` 폴더에 번호를 붙여 추가합니다.

예시:

```text
seeds/
  001_subscription_plans.sql
  002_stocks_sample.sql
  003_admin_accounts.sql
```

실행 방법:

```bash
for file in seeds/*.sql; do
  psql -d signal_alpha -f "$file"
done
```

Windows PowerShell:

```powershell
Get-ChildItem .\seeds\*.sql | Sort-Object Name | ForEach-Object {
    psql -d signal_alpha -f $_.FullName
}
```

## 7. Collector별 저장 흐름

모든 Collector는 실행 시작 시 `collector_runs`에 실행 로그를 만들고, 수집 원본 공통 메타데이터를 `raw_documents`에 저장합니다. 이후 source별 detail 테이블에 원본 상세 데이터를 저장하고, 후속 처리를 위해 `processing_queue`에 정규화 작업을 등록합니다.

| Collector | 저장 흐름 |
| --- | --- |
| DART Collector | `collector_runs` -> `raw_documents` -> `dart_raw_details` -> `processing_queue` |
| Report Collector | `collector_runs` -> `raw_documents` -> `report_raw_details` -> `report_chunks` -> `processing_queue` |
| Hiring Collector | `collector_runs` -> `raw_documents` -> `hiring_raw_details` -> `processing_queue` |
| Patent Collector | `collector_runs` -> `raw_documents` -> `patent_raw_details` -> `processing_queue` |
| DataLab Collector | `collector_runs` -> `raw_documents` -> `datalab_raw_details` -> `processing_queue` |
| Price Collector | `ohlcv_data` |

Collector는 LLM을 호출하지 않습니다. 원본 데이터 저장, 중복 방지용 `source_hash` 생성, detail 저장, 처리 큐 등록까지만 담당합니다.

MVP Report RAG 필수 흐름은 다음과 같습니다.

1. Report Collector가 `report_raw_details`에 리포트 메타데이터와 PDF 파싱 결과를 저장한다.
2. Report Collector 또는 별도 Chunking 작업이 `report_chunks`에 `chunk_text`를 저장한다.
3. Embedding 작업이 `report_chunks.embedding`에 벡터를 저장한다.
4. Report Analyst가 pgvector similarity search로 관련 청크를 검색한다.
5. 검색된 청크를 기반으로 LLM이 리포트 의견을 분석한다.
6. 분석 결과는 `signal_events`, `signal_metrics`, `analysis_results`, `agent_results`, `final_signals`로 이어진다.

## 8. Agent별 조회/저장 흐름

Normalizer는 raw 계층을 직접 분석하지 않고 정규화 계층을 생성합니다.

```text
raw_documents + detail tables
-> source_documents
-> signal_events
-> signal_metrics
-> validation_logs
```

Agent는 정규화된 `source_documents`, `signal_events`, `signal_metrics`를 조회해 분석합니다. 분석 결과는 `analysis_results`에 대표 단위로 저장하고, D-1부터 D-5까지의 방식별 결과는 `agent_results`에 저장합니다.

```text
source_documents / signal_events / signal_metrics
-> analysis_results
-> agent_results
-> ml_scores
-> final_signals
-> score_history
```

ML Scorer는 `analysis_results`, `agent_results`, 정량 점수, 기술적 분석 점수 등을 기반으로 `ml_scores`와 `score_history`를 생성합니다. Frontend는 원칙적으로 `final_signals`를 중심으로 조회합니다.

Report Analyst의 RAG 검색 예시:

```sql
SELECT
    id,
    raw_document_id,
    stock_id,
    chunk_index,
    chunk_text,
    embedding <=> :query_embedding AS distance
FROM report_chunks
WHERE stock_id = :stock_id
ORDER BY embedding <=> :query_embedding
LIMIT 5;
```

`:query_embedding`은 애플리케이션에서 생성한 embedding vector입니다. `distance`가 낮을수록 유사도가 높으며, `stock_id`로 먼저 필터링해서 다른 종목 리포트가 분석 근거에 섞이지 않게 합니다.

## 9. final_signals 조회 기준

`final_signals.is_current`는 `stock_id + signal_date + run_key` 기준 현재 대표 시그널을 의미합니다. 같은 종목, 같은 날짜, 같은 `run_key` 안에서는 `is_current = TRUE`인 row가 1개만 존재할 수 있습니다.

오전 대표 시그널:

```sql
SELECT *
FROM final_signals
WHERE stock_id = :stock_id
  AND signal_date = CURRENT_DATE
  AND run_key = 'AM'
  AND is_current = TRUE;
```

오후 대표 시그널:

```sql
SELECT *
FROM final_signals
WHERE stock_id = :stock_id
  AND signal_date = CURRENT_DATE
  AND run_key = 'PM'
  AND is_current = TRUE;
```

오늘의 최신 대표 시그널 1개:

```sql
SELECT *
FROM final_signals
WHERE stock_id = :stock_id
  AND signal_date = CURRENT_DATE
  AND is_current = TRUE
ORDER BY published_at DESC NULLS LAST, created_at DESC
LIMIT 1;
```

대표 `run_key` 기준:

| 실행 상황 | run_key |
| --- | --- |
| 오전 리포트 반영 정기 분석 | `AM` |
| 오후 리포트 반영 정기 분석 | `PM` |
| 야간 배치 분석 | `BATCH_NIGHT` |
| DART 고임팩트 즉시 분석 | `IMMEDIATE` |
| 수동 재분석 | `MANUAL` |

## 10. 주의사항

- Signal Alpha가 제공하는 시그널은 AI Agent의 데이터 분석 결과이며 투자 권유가 아닙니다.
- 투자 판단과 손실 책임은 사용자 본인에게 있습니다.
- 수치 데이터는 LLM이 생성하지 않습니다.
- 점수, 지표, 가격, 변화율, 재무 수치 등 정량 데이터는 반드시 DB에 저장된 값만 사용합니다.
- 최종 설명 문장과 요약은 LLM이 생성할 수 있지만, 근거가 되는 숫자는 `signal_metrics`, `ohlcv_data`, `fundamentals`, `analysis_results`, `ml_scores`, `final_signals` 등 DB 값에서만 가져와야 합니다.
- Report RAG를 MVP에서 구현하므로 `pgvector` extension은 필수입니다.
- 로컬 PostgreSQL에서 `vector` extension이 설치되어 있지 않으면 `CREATE EXTENSION vector` 단계에서 실패할 수 있습니다.
- Neon, Supabase, Docker PostgreSQL 등 pgvector 지원 환경을 사용해야 합니다.
- `pgvector` extension이 PostgreSQL 서버에 설치되어 있어야 `VECTOR(1024)` 컬럼과 `ivfflat` 인덱스를 사용할 수 있습니다.
- 개발 초기에는 데이터가 적어도 `idx_chunks_embedding` 생성은 가능하지만, 실제 검색 품질은 청크 데이터가 쌓인 뒤 더 안정적입니다.
- 청크 데이터가 많이 쌓인 뒤에는 필요하면 `idx_chunks_embedding`을 `DROP INDEX` 후 재생성할 수 있습니다.
- embedding vector 차원이 바뀌면 `report_chunks.embedding`의 `VECTOR(1024)` 차원도 함께 바꿔야 합니다.
- PostgreSQL 배열 컬럼은 각 원소에 대한 외래키 무결성을 강제하지 못합니다.
- 배열 한계가 있는 컬럼은 `processing_queue.source_raw_ids`, `processing_queue.source_signal_event_ids`, `processing_queue.source_analysis_result_ids`, `analysis_results.source_signal_event_ids`, `agent_results.source_signal_event_ids`입니다.
- MVP에서는 빠른 구현을 위해 배열을 유지하고, `validation_logs`로 source trace 검증을 기록합니다.
- 추후 데이터 구조가 안정화되면 `analysis_signal_events`, `agent_signal_events` 같은 매핑 테이블로 분리합니다.
- `source_hash`, `event_hash`, unique constraint, partial unique index를 통해 중복 저장을 방지합니다.
