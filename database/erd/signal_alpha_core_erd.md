# Signal Alpha ERD

베이스라인 마이그레이션(`database/migrations/` 001~013) 기준 전체 테이블 관계도.

> 컬럼 전체 정의의 기준은 항상 `database/migrations/` SQL입니다.
> 이 문서는 테이블 간 관계와 핵심 컬럼(PK/FK/UNIQUE/대표 필드)만 표기합니다.
> **새 테이블을 추가하는 마이그레이션 PR은 이 문서의 해당 Zone 블록도 함께 갱신해야 합니다** (`database/README.md` §4).

## Zone A — Market (002_market.sql)

```mermaid
erDiagram
    stocks {
        BIGINT id PK
        VARCHAR ticker UK
        VARCHAR name
        VARCHAR market "KOSPI|KOSDAQ"
        VARCHAR sector
        BOOLEAN is_active
        BOOLEAN is_target "수집 대상 스위치"
        VARCHAR short_name "DataLab 약칭"
    }

    ohlcv_data {
        BIGINT id PK
        BIGINT stock_id FK
        DATE trade_date "UK(stock_id,trade_date)"
        NUMERIC open_high_low_close
        BIGINT volume
        BIGINT foreign_net
        BIGINT institution_net
    }

    fundamentals {
        BIGINT id PK
        BIGINT stock_id FK
        DATE fiscal_date "UK(stock_id,fiscal_date,period_type)"
        VARCHAR period_type "annual|quarter"
        BIGINT revenue
        NUMERIC per_pbr_roe_roa
    }

    price_snapshots {
        BIGINT id PK
        BIGINT stock_id FK
        TIMESTAMPTZ captured_at "UK(stock_id,captured_at)"
        DATE trade_date
        NUMERIC current_price
        BIGINT volume "당일 누적"
    }

    stocks ||--o{ ohlcv_data : "stock_id"
    stocks ||--o{ fundamentals : "stock_id"
    stocks ||--o{ price_snapshots : "stock_id"
```

## Zone C — Collection 핵심 (003_collection_core.sql)

```mermaid
erDiagram
    collector_runs {
        BIGINT id PK
        VARCHAR collector_type "DART|REPORT|HIRING|PATENT|DATALAB|PRICE"
        VARCHAR run_mode "batch|immediate|manual"
        VARCHAR status
    }

    raw_documents {
        BIGINT id PK
        BIGINT stock_id FK
        BIGINT collector_run_id FK
        VARCHAR source_type "UK(source_type,external_id)"
        VARCHAR external_id
        VARCHAR source_hash UK
        TIMESTAMPTZ published_at
    }

    dart_raw_details {
        BIGINT raw_document_id PK "복합FK(raw_document_id,stock_id) CASCADE"
        BIGINT stock_id
        VARCHAR receipt_no UK
        VARCHAR disclosure_type
        JSONB extra_payload
    }

    report_raw_details {
        BIGINT raw_document_id PK "복합FK CASCADE"
        BIGINT stock_id
        VARCHAR securities_firm
        DATE publish_date
        INTEGER target_price
        VARCHAR parsing_status
    }

    hiring_raw_details {
        BIGINT raw_document_id PK "복합FK CASCADE"
        BIGINT stock_id
        VARCHAR keyword
        INTEGER job_count
        NUMERIC change_pct
    }

    patent_raw_details {
        BIGINT raw_document_id PK "복합FK CASCADE"
        BIGINT stock_id
        VARCHAR application_no UK
        DATE application_date
        VARCHAR tech_category
    }

    report_chunks {
        BIGINT id PK
        BIGINT raw_document_id FK "복합FK CASCADE, UK(raw_document_id,chunk_index)"
        BIGINT stock_id
        INTEGER chunk_index
        TEXT chunk_text
        VECTOR embedding "1024, ivfflat"
    }

    collector_runs ||--o{ raw_documents : "collector_run_id"
    raw_documents ||--o| dart_raw_details : "raw_document_id"
    raw_documents ||--o| report_raw_details : "raw_document_id"
    raw_documents ||--o| hiring_raw_details : "raw_document_id"
    raw_documents ||--o| patent_raw_details : "raw_document_id"
    raw_documents ||--o{ report_chunks : "raw_document_id"
```

`stocks ||--o{ raw_documents` (stock_id). detail 테이블의 복합 FK `(raw_document_id, stock_id) → raw_documents(id, stock_id)`는 detail 행의 종목이 원본 문서와 일치함을 DB 차원에서 보장한다.

## Zone C — DART 보조 (004_collection_dart.sql)

```mermaid
erDiagram
    dart_corp_codes {
        BIGINT id PK
        BIGINT stock_id FK "nullable"
        VARCHAR corp_code UK
        VARCHAR ticker UK
        VARCHAR corp_name
        TIMESTAMPTZ synced_at
    }

    dart_collection_states {
        BIGINT stock_id PK "FK stocks CASCADE"
        VARCHAR ticker UK
        DATE last_bgn_de
        DATE last_end_de
        BIGINT last_collector_run_id FK
    }
```

## Zone C — DataLab (005_collection_datalab.sql) · Hiring (006_collection_hiring.sql)

DataLab은 **카테고리 단위 수집**: 종목 기반 `raw_documents`를 쓰지 않고 자체 원본/상세 테이블을 사용한다.

```mermaid
erDiagram
    datalab_categories {
        BIGINT id PK
        VARCHAR name UK
        VARCHAR sector
        BOOLEAN is_active
    }

    datalab_category_stocks {
        BIGINT category_id PK "FK CASCADE"
        BIGINT stock_id PK "FK stocks"
        NUMERIC weight
    }

    datalab_category_keywords {
        BIGINT category_id PK "FK CASCADE"
        VARCHAR keyword PK
        VARCHAR keyword_group
        BOOLEAN is_active
    }

    datalab_raw_documents {
        BIGINT id PK
        BIGINT category_id FK
        BIGINT collector_run_id FK
        VARCHAR source_hash UK
        VARCHAR external_id "UK(source_name,external_id)"
    }

    datalab_raw_details {
        BIGINT raw_document_id PK "FK datalab_raw_documents CASCADE"
        BIGINT category_id FK
        VARCHAR keyword "UK(category_id,keyword,observed_date,period_type,device,gender,age_group)"
        DATE observed_date
        NUMERIC search_index
        BOOLEAN is_spike
    }

    hiring_baseline {
        BIGINT id PK
        BIGINT stock_id FK "UK, stocks CASCADE"
        NUMERIC avg_search_volume
        NUMERIC q1_q4_factor
    }

    datalab_categories ||--o{ datalab_category_stocks : "category_id"
    datalab_categories ||--o{ datalab_category_keywords : "category_id"
    datalab_categories ||--o{ datalab_raw_documents : "category_id"
    datalab_raw_documents ||--o| datalab_raw_details : "raw_document_id"
    datalab_categories ||--o{ datalab_raw_details : "category_id"
```

## Zone D — Processing (007_processing.sql)

```mermaid
erDiagram
    processing_queue {
        BIGINT id PK
        BIGINT stock_id FK "nullable — DataLab은 카테고리 단위 인큐"
        VARCHAR task_type
        VARCHAR status
        BIGINT_ARRAY source_raw_ids "FK 미강제(배열)"
        JSONB task_context
    }

    dead_letter {
        BIGINT id PK
        BIGINT processing_queue_id FK "UK — 멱등 아카이브 (005)"
        BIGINT stock_id FK "nullable"
        VARCHAR task_type
        JSONB task_context "replay payload"
        TIMESTAMPTZ replayed_at "nullable"
        BIGINT replayed_task_id FK "재등록된 큐 태스크"
    }

    source_documents {
        BIGINT id PK
        BIGINT raw_document_id UK "복합FK CASCADE"
        BIGINT stock_id
        VARCHAR source_type
        VARCHAR reliability_level
        BOOLEAN is_official
    }

    signal_events {
        BIGINT id PK
        BIGINT stock_id FK
        BIGINT source_document_id FK
        VARCHAR event_hash UK
        VARCHAR event_type
        VARCHAR signal_direction
        VARCHAR impact_level
    }

    signal_metrics {
        BIGINT id PK
        BIGINT signal_event_id FK "CASCADE, UK(signal_event_id,metric_name)"
        VARCHAR metric_name
        NUMERIC metric_value
    }

    validation_logs {
        BIGINT id PK
        VARCHAR target_type
        BIGINT target_id_int "둘 중 하나만 NOT NULL"
        UUID target_id_uuid
        BOOLEAN passed
    }

    source_documents ||--o{ signal_events : "source_document_id"
    signal_events ||--o{ signal_metrics : "signal_event_id"
    processing_queue ||--o| dead_letter : "processing_queue_id (종착 실패 격리)"
```

## Zone B+F — User / Billing (008_users_billing_base.sql, 010_users_billing_extend.sql)

```mermaid
erDiagram
    users {
        BIGINT id PK
        VARCHAR member_code UK
        VARCHAR email UK
        BOOLEAN agreed_risk
        TIMESTAMPTZ deleted_at
    }

    subscription_plans {
        BIGINT id PK
        VARCHAR plan_type UK "free|pro|premium"
        INTEGER max_watchlist
    }

    signal_subscriptions {
        BIGINT id PK
        BIGINT user_id FK "active 1건만(부분 UK)"
        BIGINT plan_id FK
        VARCHAR status
    }

    watchlists {
        BIGINT id PK
        BIGINT user_id FK "UK(user_id,stock_id)"
        BIGINT stock_id FK
    }

    signal_journals {
        BIGINT id PK
        BIGINT user_id FK
        BIGINT final_signal_id FK "nullable"
        BIGINT stock_id FK
        VARCHAR user_view
    }

    user_signal_reads {
        BIGINT id PK
        BIGINT user_id FK "UK(user_id,final_signal_id)"
        BIGINT final_signal_id FK
    }

    user_sessions {
        BIGINT id PK
        BIGINT user_id FK "users CASCADE"
        TEXT refresh_token_hash UK
        TIMESTAMPTZ expires_at
        TIMESTAMPTZ revoked_at
    }

    social_accounts {
        BIGINT id PK
        BIGINT user_id FK "users CASCADE"
        VARCHAR provider "UK(provider,provider_user_id)"
        VARCHAR provider_user_id
    }

    portone_verifications {
        BIGINT id PK
        BIGINT user_id FK
        VARCHAR imp_uid UK
        VARCHAR verification_type
    }

    terms_agreements {
        BIGINT id PK
        BIGINT user_id FK "UK(user_id,terms_type,version)"
        VARCHAR terms_type
        VARCHAR version
    }

    users ||--o{ signal_subscriptions : "user_id"
    subscription_plans ||--o{ signal_subscriptions : "plan_id"
    users ||--o{ watchlists : "user_id"
    users ||--o{ signal_journals : "user_id"
    users ||--o{ user_signal_reads : "user_id"
    users ||--o{ user_sessions : "user_id"
    users ||--o{ social_accounts : "user_id"
    users ||--o{ portone_verifications : "user_id"
    users ||--o{ terms_agreements : "user_id"
```

`stocks ||--o{ watchlists`, `final_signals ||--o{ signal_journals / user_signal_reads` (Zone E 참조).

## Zone E — Analysis (009_analysis.sql)

```mermaid
erDiagram
    analysis_requests {
        BIGINT id PK
        BIGINT user_id FK "nullable"
        BIGINT stock_id FK
        VARCHAR status
        VARCHAR analysis_mode "full|dart_only|quick"
    }

    analysis_results {
        BIGINT id PK
        BIGINT request_id FK "nullable"
        BIGINT stock_id FK
        DATE analysis_date "UK(stock_id,analysis_date,analysis_mode,run_key,version)"
        VARCHAR run_key
        BIGINT_ARRAY source_signal_event_ids "FK 미강제(배열)"
        NUMERIC base_score "0~100"
        TEXT disclaimer "법적 고지 필수"
    }

    quant_scores {
        BIGINT id PK
        BIGINT result_id FK "UK"
        JSONB score_breakdown
        VARCHAR source_agreement "HIGH|MEDIUM|LOW"
    }

    ta_scores {
        BIGINT id PK
        BIGINT result_id FK "UK"
        NUMERIC ta_score
    }

    ai_scores {
        BIGINT id PK
        BIGINT result_id FK "UK"
        NUMERIC dart_report_alt_scores
    }

    agent_results {
        BIGINT id PK
        BIGINT result_id FK "UK(result_id,debate_method)"
        VARCHAR debate_method "D-1~D-5"
        NUMERIC method_score
        JSONB method_detail
    }

    xgb_model_versions {
        BIGINT id PK
        VARCHAR model_version UK
        BOOLEAN is_active "TRUE 1건만(부분 UK)"
    }

    ml_scores {
        BIGINT id PK
        BIGINT result_id FK "UK"
        BIGINT model_version_id FK
        NUMERIC calibrated_score
    }

    final_signals {
        BIGINT id PK
        BIGINT stock_id FK
        BIGINT analysis_result_id FK
        DATE signal_date "UK(stock_id,signal_date,run_key,version)"
        VARCHAR run_key
        BOOLEAN is_current "조합당 1건(부분 UK + 트리거)"
        NUMERIC final_score
        VARCHAR signal
        BOOLEAN is_published
        TEXT disclaimer "법적 고지 필수"
    }

    score_history {
        BIGINT id PK
        BIGINT stock_id FK
        BIGINT final_signal_id FK "nullable, 둘 중 하나 필수"
        BIGINT analysis_result_id FK "nullable"
        NUMERIC final_score
    }

    backtest_results {
        BIGINT id PK
        BIGINT final_signal_id FK
        BIGINT stock_id FK
        NUMERIC change_pct_5d
        BOOLEAN is_hit
    }

    analysis_requests ||--o{ analysis_results : "request_id"
    analysis_results ||--o| quant_scores : "result_id"
    analysis_results ||--o| ta_scores : "result_id"
    analysis_results ||--o| ai_scores : "result_id"
    analysis_results ||--o{ agent_results : "result_id"
    analysis_results ||--o| ml_scores : "result_id"
    xgb_model_versions ||--o{ ml_scores : "model_version_id"
    analysis_results ||--o{ final_signals : "analysis_result_id"
    final_signals ||--o{ score_history : "final_signal_id"
    final_signals ||--o{ backtest_results : "final_signal_id"
```

## Zone G — Admin (011_admin.sql)

```mermaid
erDiagram
    admin_accounts {
        BIGINT id PK
        VARCHAR email UK
        TEXT password_hash
        BOOLEAN is_active
    }

    admin_sessions {
        BIGINT id PK
        BIGINT admin_id FK
        VARCHAR session_token UK
        TIMESTAMPTZ expires_at
    }

    admin_accounts ||--o{ admin_sessions : "admin_id"
```

## Legacy — report MVP (013_legacy_report_mvp.sql) ⚠️ 폐기 예정

신규 코드 참조 금지. 공용 경로(`raw_documents` → `report_raw_details` → `report_chunks`)로 이전 후 DROP 예정.

```mermaid
erDiagram
    report_raw {
        BIGINT id PK
        VARCHAR stock_code "UK(firm,date,stock_code)"
        VARCHAR firm
        VARCHAR date "문자열 날짜(레거시)"
        INT target_price
    }

    report_signal {
        BIGINT id PK
        VARCHAR stock_code
        VARCHAR direction
        FLOAT score
        JSONB opinions
    }
```

## Zone C·E — Hiring / Alternative 확장 (014~020)

베이스라인(001~013) 이후 추가된 Hiring 분석 계층과 Alternative 통합 신호 확장.

```mermaid
erDiagram
    hiring_signals {
        BIGINT id PK
        BIGINT stock_id FK
        DATE observed_date "UK(stock_id,observed_date)"
        INTEGER job_count
        NUMERIC relative_strength
        BOOLEAN is_spike "레거시 일별 채용 강도 (015)"
    }

    hiring_sources {
        BIGINT id PK
        BIGINT stock_id FK
        ENUM crawler_type "UK(stock_id,crawler_type) (016)"
        VARCHAR crawler_class
        JSONB extra_config
        BOOLEAN is_active
    }

    hiring_job_functions {
        BIGINT id PK
        VARCHAR function_key UK "ENGINEER|SALES|… 안정 키 (020)"
        VARCHAR label
        BOOLEAN is_active
    }

    hiring_job_function_stocks {
        BIGINT job_function_id PK,FK "→ hiring_job_functions"
        BIGINT stock_id PK,FK "→ stocks"
        NUMERIC weight "직군 노출 가중치 (020)"
    }

    stocks ||--o{ hiring_signals : ""
    stocks ||--o{ hiring_sources : ""
    stocks ||--o{ hiring_job_function_stocks : ""
    hiring_job_functions ||--o{ hiring_job_function_stocks : ""
```

기존 테이블에 추가된 컬럼(신규 테이블 아님):

- `final_signals` ← `consensus_score`, `positive_evidence`, `caution_evidence` (017). `alignment_rate`는 기존 `source_agreement`와 동일하여 컬럼 미저장(읽기 계층 alias).
- `datalab_category_keywords` ← `polarity` (demand/risk/neutral) (018).
- `patent_raw_details` ← `llm_features` JSONB, `llm_status` (019).
- `hiring_raw_details` ← `observed_date` (014).

## 시스템 테이블

- `schema_migrations(filename PK, checksum, applied_at)` — `database/migrate.py`가 자동 생성·관리하는 적용 원장. 마이그레이션 파일로 만들지 않는다.
