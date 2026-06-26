-- 001_baseline.sql
-- ============================================================================
-- Signal α 베이스라인 스키마 (개발 단계 통합본)
-- ----------------------------------------------------------------------------
-- 기존 001~021 마이그레이션을 하나의 베이스라인으로 squash 한 파일이다 (총 52개 테이블).
-- 개발 단계라 운영 데이터가 없으므로 증분 마이그레이션 이력을 단일화한다.
--
-- 통합 규칙(자세한 내용은 database/docs/migration_rules.md):
--   - ALTER류는 대상 CREATE TABLE에 흡수했다:
--       014→hiring_raw_details.observed_date, 017→final_signals(consensus_score/
--       positive_evidence/caution_evidence), 018→datalab_category_keywords.polarity,
--       019→patent_raw_details(llm_features/llm_status+부분인덱스),
--       021→hiring_signals.calculation_phase.
--   - 020 신규 테이블(hiring_job_functions, hiring_job_function_stocks)·트리거 포함.
--   - 015/016/021의 `IF NOT EXISTS`는 컨벤션(§3)대로 제거했다 (fresh DB 기준).
--   - 016의 종목별 크롤러 시드 INSERT는 seeds/005_seed_hiring_sources.sql로 분리했다.
--   - 013 레거시(report_raw/report_signal)는 아직 report 파이프라인 코드가 사용 중이라
--     맨 아래 Legacy 섹션으로 보존한다 (폐기 예정, 신규 참조 금지).
--
-- ⚠️ 이 파일을 적용한 적이 있는 DB는 checksum 원장과 충돌한다.
--    기존 dev DB는 반드시 재생성한다: docker compose down -v && up -d postgres
-- ============================================================================

-- 확장 없음. (이전엔 report_chunks.embedding 용 pgvector 확장을 만들었으나
-- 임베딩/벡터화 기능 제거로 더 이상 필요 없다.)

-- ============================================================================
-- Zone A — Market: 종목 마스터 + 시세 데이터
-- ============================================================================
-- stocks.is_target: 수집 대상 스위치 (시드는 seeds/001_seed_stocks.sql).
-- price_snapshots: 키움 REST 장중 폴링 관측값. 일봉 확정치는 ohlcv_data가 담당.

CREATE TABLE stocks (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    market VARCHAR(10) NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ')),
    sector VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_target BOOLEAN NOT NULL DEFAULT FALSE,
    short_name VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stocks_is_target
    ON stocks (is_target)
    WHERE is_target = TRUE;

CREATE TABLE ohlcv_data (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    trade_date DATE NOT NULL,
    open NUMERIC(12,2) NOT NULL,
    high NUMERIC(12,2) NOT NULL,
    low NUMERIC(12,2) NOT NULL,
    close NUMERIC(12,2) NOT NULL,
    volume BIGINT NOT NULL,
    adjusted_close NUMERIC(12,2),
    foreign_net BIGINT,
    institution_net BIGINT,
    change_pct NUMERIC(6,2),
    market_cap BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ohlcv UNIQUE (stock_id, trade_date)
);

CREATE INDEX idx_ohlcv_stock_date
    ON ohlcv_data (stock_id, trade_date DESC);

CREATE TABLE fundamentals (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    fiscal_date DATE NOT NULL,
    period_type VARCHAR(10) NOT NULL CHECK (period_type IN ('annual', 'quarter')),
    revenue BIGINT,
    net_income BIGINT,
    operating_margin NUMERIC(8,2),
    eps NUMERIC(10,2),
    bps NUMERIC(10,2),
    per NUMERIC(8,2),
    pbr NUMERIC(8,2),
    roe NUMERIC(8,2),
    roa NUMERIC(8,2),
    debt_ratio NUMERIC(8,2),
    source VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fundamentals UNIQUE (stock_id, fiscal_date, period_type)
);

CREATE TABLE price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    captured_at TIMESTAMPTZ NOT NULL,
    trade_date DATE NOT NULL,
    current_price NUMERIC(12,2) NOT NULL,
    open NUMERIC(12,2),
    high NUMERIC(12,2),
    low NUMERIC(12,2),
    volume BIGINT,                -- 당일 누적 거래량 (주)
    trade_value BIGINT,           -- 당일 누적 거래대금 (백만원)
    market_cap BIGINT,            -- 시가총액 (억원)
    shares_outstanding BIGINT,    -- 상장주수 (천주)
    per NUMERIC(10,2),
    pbr NUMERIC(10,2),
    eps NUMERIC(12,2),
    bps NUMERIC(12,2),
    roe NUMERIC(8,2),
    roa NUMERIC(8,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_price_snapshot UNIQUE (stock_id, captured_at)
);

CREATE INDEX idx_price_snapshots_stock_time
    ON price_snapshots (stock_id, captured_at DESC);

CREATE INDEX idx_price_snapshots_trade_date
    ON price_snapshots (trade_date);


-- ============================================================================
-- Zone C — Collection 핵심: 수집 실행 이력 + 종목 단위 원본 문서/상세
-- ============================================================================
-- raw_documents.uq_raw_document_stock UNIQUE(id, stock_id)는 detail 테이블의
-- 복합 FK 대상이다 (detail 행의 stock_id가 원본 문서와 일치함을 DB가 보장).
-- detail 테이블 ON DELETE CASCADE: 원본 문서 삭제 시 상세도 함께 삭제.

CREATE TABLE collector_runs (
    id BIGSERIAL PRIMARY KEY,
    collector_type VARCHAR(20) NOT NULL
        CHECK (collector_type IN ('DART', 'REPORT', 'HIRING', 'PATENT', 'DATALAB', 'PRICE')),
    run_mode VARCHAR(20) NOT NULL
        CHECK (run_mode IN ('batch', 'immediate', 'manual')),
    status VARCHAR(20) NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    collected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_collector_runs_type_time
    ON collector_runs (collector_type, started_at DESC);

CREATE INDEX idx_collector_runs_status
    ON collector_runs (status, started_at DESC)
    WHERE status != 'success';

CREATE TABLE raw_documents (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    collector_run_id BIGINT REFERENCES collector_runs(id),
    source_type VARCHAR(20) NOT NULL
        CHECK (source_type IN ('DART', 'REPORT', 'HIRING', 'PATENT', 'DATALAB')),
    source_name VARCHAR(100) NOT NULL,
    external_id VARCHAR(200) NOT NULL,
    source_hash VARCHAR(64) NOT NULL UNIQUE,
    title TEXT NOT NULL,
    source_url TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    collect_status VARCHAR(20) NOT NULL DEFAULT 'success'
        CHECK (collect_status IN ('success', 'partial', 'failed')),
    collect_error TEXT,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    collector_ver VARCHAR(20) NOT NULL DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_raw_document UNIQUE (source_type, external_id),
    CONSTRAINT uq_raw_document_stock UNIQUE (id, stock_id)
);

CREATE INDEX idx_raw_doc_stock_source
    ON raw_documents (stock_id, source_type, published_at DESC);

CREATE INDEX idx_raw_doc_collect_fail
    ON raw_documents (collect_status, created_at DESC)
    WHERE collect_status != 'success';

CREATE INDEX idx_raw_doc_run
    ON raw_documents (collector_run_id)
    WHERE collector_run_id IS NOT NULL;

CREATE TABLE dart_raw_details (
    raw_document_id BIGINT PRIMARY KEY,
    stock_id BIGINT NOT NULL,
    receipt_no VARCHAR(30) NOT NULL UNIQUE,
    corp_code VARCHAR(20),
    report_name TEXT NOT NULL,
    disclosure_type VARCHAR(50),
    priority VARCHAR(10) NOT NULL DEFAULT 'batch'
        CHECK (priority IN ('immediate', 'batch')),
    priority_reason VARCHAR(200),
    is_correction BOOLEAN NOT NULL DEFAULT FALSE,
    original_receipt_no VARCHAR(30),
    extra_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_dart_stock
    ON dart_raw_details (stock_id);

CREATE INDEX idx_dart_type
    ON dart_raw_details (disclosure_type);

CREATE INDEX idx_dart_priority
    ON dart_raw_details (priority);

CREATE TABLE report_raw_details (
    raw_document_id BIGINT PRIMARY KEY,
    stock_id BIGINT NOT NULL,
    securities_firm VARCHAR(100) NOT NULL,
    analyst_name VARCHAR(100),
    publish_date DATE NOT NULL,
    investment_opinion VARCHAR(20),
    target_price INTEGER,
    previous_target_price INTEGER,
    current_price_at_publish INTEGER,
    upside_pct NUMERIC(6,2),
    has_pdf BOOLEAN NOT NULL DEFAULT FALSE,
    pdf_url TEXT,
    local_file_path TEXT,
    extracted_text TEXT,
    extracted_text_path TEXT,
    parsing_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (parsing_status IN ('pending', 'success', 'failed', 'skipped')),
    parsing_error TEXT,
    extra_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_report_detail_stock
    ON report_raw_details (stock_id, publish_date DESC);

CREATE INDEX idx_report_detail_firm
    ON report_raw_details (securities_firm, stock_id);

-- hiring_raw_details: observed_date는 014에서 추가된 컬럼을 흡수한 것.
-- HiringAnalyzer가 날짜 기반 필터링(14일 이동평균, 오늘 공고 수)에 사용.
CREATE TABLE hiring_raw_details (
    raw_document_id BIGINT PRIMARY KEY,
    stock_id BIGINT NOT NULL,
    keyword VARCHAR(100),
    job_category VARCHAR(100),
    job_count INTEGER,
    previous_job_count INTEGER,
    change_pct NUMERIC(8,2),
    extra_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 014에서 추가된 컬럼. 순차적용(ALTER ADD)과 순서를 맞추기 위해 맨 끝에 둔다.
    observed_date DATE NOT NULL,
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_hiring_stock_keyword
    ON hiring_raw_details (stock_id, keyword);

CREATE INDEX idx_hiring_change
    ON hiring_raw_details (stock_id, change_pct DESC);

CREATE INDEX idx_hiring_observed
    ON hiring_raw_details (stock_id, observed_date DESC);

CREATE TABLE patent_raw_details (
    raw_document_id BIGINT PRIMARY KEY,
    stock_id BIGINT NOT NULL,
    application_no VARCHAR(30) NOT NULL UNIQUE,
    patent_title TEXT NOT NULL,
    applicant_name VARCHAR(200),
    application_date DATE NOT NULL,
    tech_category VARCHAR(50),
    is_new_category BOOLEAN NOT NULL DEFAULT FALSE,
    extra_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 019: LLM 보강 캐시 (별도 enrichment 도구가 채움. analyzer는 캐시 read).
    --   순차적용(ALTER ADD)과 컬럼 순서를 맞추기 위해 맨 끝에 둔다.
    llm_features JSONB,
    llm_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (llm_status IN ('pending', 'success', 'failed', 'skipped')),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_patent_stock_date
    ON patent_raw_details (stock_id, application_date DESC);

CREATE INDEX idx_patent_stock_tech
    ON patent_raw_details (stock_id, tech_category);

CREATE INDEX idx_patent_new_category
    ON patent_raw_details (stock_id, is_new_category)
    WHERE is_new_category = TRUE;

-- 019: enrichment worklist (pending subset only — 완료되면 비어가는 부분 인덱스).
CREATE INDEX idx_patent_llm_pending
    ON patent_raw_details (application_date DESC, raw_document_id DESC)
    WHERE llm_status = 'pending';

-- (report_chunks 테이블 제거됨 — 리포트 PDF 임베딩/RAG 검색 기능 폐지.)


-- ============================================================================
-- Zone C — DART 보조: 고유번호 매핑 + 종목별 수집 상태
-- ============================================================================

CREATE TABLE dart_corp_codes (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL UNIQUE,
    ticker VARCHAR(10) NOT NULL,
    corp_name VARCHAR(200) NOT NULL,
    corp_name_eng VARCHAR(200),
    stock_name VARCHAR(200),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dart_corp_ticker UNIQUE (ticker)
);

CREATE INDEX idx_dart_corp_codes_stock
    ON dart_corp_codes (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX idx_dart_corp_codes_ticker
    ON dart_corp_codes (ticker);

CREATE TABLE dart_collection_states (
    stock_id BIGINT PRIMARY KEY REFERENCES stocks(id) ON DELETE CASCADE,
    ticker VARCHAR(10) NOT NULL,
    last_bgn_de DATE NOT NULL,
    last_end_de DATE NOT NULL,
    last_receipt_no VARCHAR(30),
    last_collected_count INTEGER NOT NULL DEFAULT 0,
    last_collector_run_id BIGINT REFERENCES collector_runs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_dart_collection_states_ticker
    ON dart_collection_states (ticker);

CREATE INDEX idx_dart_collection_states_end_de
    ON dart_collection_states (last_end_de DESC);


-- ============================================================================
-- Zone C — DataLab: 카테고리 기반 수집
-- ============================================================================
-- DataLab은 종목이 아닌 카테고리(검색 테마) 단위로 수집한다. 원본 문서는
-- datalab_raw_documents에, 상세는 datalab_raw_details에 category_id로 저장하고,
-- Normalizer가 datalab_category_stocks 매핑으로 카테고리 → 종목을 해석한다.

CREATE TABLE datalab_categories (
    id          BIGSERIAL    PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    sector      VARCHAR(100),
    description TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_datalab_categories_active
    ON datalab_categories (is_active)
    WHERE is_active = TRUE;

CREATE TABLE datalab_category_stocks (
    category_id BIGINT       NOT NULL REFERENCES datalab_categories(id) ON DELETE CASCADE,
    stock_id    BIGINT       NOT NULL REFERENCES stocks(id),
    weight      NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (category_id, stock_id)
);

CREATE INDEX idx_datalab_cat_stocks_stock
    ON datalab_category_stocks (stock_id);

CREATE TABLE datalab_category_keywords (
    category_id   BIGINT       NOT NULL REFERENCES datalab_categories(id) ON DELETE CASCADE,
    keyword       VARCHAR(200) NOT NULL,
    keyword_group VARCHAR(100) NOT NULL,
    source        VARCHAR(50)  NOT NULL DEFAULT 'reviewed',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- 018: 검색량 방향성 태깅 (demand=수요↑positive, risk=리스크↑negative, neutral).
    --   순차적용(ALTER ADD)과 컬럼 순서를 맞추기 위해 맨 끝에 둔다.
    polarity      VARCHAR(10)  NOT NULL DEFAULT 'demand'
        CHECK (polarity IN ('demand', 'risk', 'neutral')),
    PRIMARY KEY (category_id, keyword)
);

CREATE INDEX idx_datalab_category_keywords_active
    ON datalab_category_keywords (category_id, is_active)
    WHERE is_active = TRUE;

CREATE TABLE datalab_raw_documents (
    id               BIGSERIAL    PRIMARY KEY,
    category_id      BIGINT       NOT NULL REFERENCES datalab_categories(id),
    collector_run_id BIGINT       REFERENCES collector_runs(id),
    source_name      VARCHAR(100) NOT NULL,
    external_id      VARCHAR(500) NOT NULL,
    source_hash      VARCHAR(64)  NOT NULL UNIQUE,
    title            TEXT         NOT NULL,
    source_url       TEXT,
    published_at     TIMESTAMPTZ  NOT NULL,
    collect_status   VARCHAR(20)  NOT NULL DEFAULT 'success'
        CHECK (collect_status IN ('success', 'partial', 'failed')),
    collect_error    TEXT,
    collected_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    collector_ver    VARCHAR(20)  NOT NULL DEFAULT '1.0',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_datalab_raw_doc UNIQUE (source_name, external_id)
);

CREATE INDEX idx_datalab_raw_doc_category
    ON datalab_raw_documents (category_id, published_at DESC);

CREATE INDEX idx_datalab_raw_doc_run
    ON datalab_raw_documents (collector_run_id)
    WHERE collector_run_id IS NOT NULL;

CREATE TABLE datalab_raw_details (
    raw_document_id       BIGINT       PRIMARY KEY
        REFERENCES datalab_raw_documents(id) ON DELETE CASCADE,
    category_id           BIGINT       NOT NULL REFERENCES datalab_categories(id),
    keyword               VARCHAR(100) NOT NULL,
    keyword_group         VARCHAR(100),
    observed_date         DATE         NOT NULL,
    search_index          NUMERIC(6,2) NOT NULL,
    previous_search_index NUMERIC(6,2),
    change_pct            NUMERIC(8,2),
    period_type           VARCHAR(10)  NOT NULL DEFAULT 'daily'
        CHECK (period_type IN ('daily', 'weekly', 'monthly')),
    device                VARCHAR(10)  NOT NULL DEFAULT 'all'
        CHECK (device IN ('pc', 'mobile', 'all')),
    gender                VARCHAR(5)   NOT NULL DEFAULT 'all'
        CHECK (gender IN ('m', 'f', 'all')),
    age_group             VARCHAR(20)  NOT NULL DEFAULT 'all',
    is_spike              BOOLEAN      NOT NULL DEFAULT FALSE,
    extra_payload         JSONB,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_datalab UNIQUE (
        category_id,
        keyword,
        observed_date,
        period_type,
        device,
        gender,
        age_group
    )
);

CREATE INDEX idx_datalab_category_date
    ON datalab_raw_details (category_id, observed_date DESC);

CREATE INDEX idx_datalab_spike
    ON datalab_raw_details (category_id, is_spike)
    WHERE is_spike = TRUE;


-- ============================================================================
-- Zone C — Hiring: 계절성 기준선 + 분석 시그널 + 크롤러 소스
-- ============================================================================
-- hiring_baseline: 네이버 DataLab 기반 채용 트렌드 계절성 기준선.
--   분기별 가중치(qX_factor) × 평균 검색량(avg_search_volume) = seasonal_baseline.
-- hiring_signals(구 015): HiringAnalyzer 분석 결과.
-- hiring_sources(구 016): 기업별 공식 채용 사이트 크롤러 설정.
--   ticker가 Single Source of Truth. 종목별 시드는 seeds/005_seed_hiring_sources.sql.

CREATE TABLE hiring_baseline (
    id                 BIGSERIAL PRIMARY KEY,
    stock_id           BIGINT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    avg_search_volume  NUMERIC(10,2) NOT NULL DEFAULT 0,
    q1_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    q2_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    q3_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    q4_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    keyword_group_name VARCHAR(200),
    data_start_date    DATE,
    data_end_date      DATE,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hiring_baseline_stock UNIQUE (stock_id)
);

CREATE INDEX idx_hiring_baseline_stock
    ON hiring_baseline (stock_id);

CREATE TABLE hiring_signals (
    id                BIGSERIAL PRIMARY KEY,
    stock_id          BIGINT NOT NULL REFERENCES stocks(id),
    observed_date     DATE NOT NULL,
    job_count         INTEGER NOT NULL DEFAULT 0,
    baseline          NUMERIC(10,2),
    relative_strength NUMERIC(8,4),
    is_spike          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 021: 분석 근거 Phase. 순차적용(ALTER ADD)과 순서/제약 이름을 맞추기 위해
    --   맨 끝 컬럼 + 인라인 UNIQUE(자동이름 ..._stock_id_observed_date_key)로 둔다.
    calculation_phase VARCHAR(1),
    UNIQUE (stock_id, observed_date)
);

COMMENT ON COLUMN hiring_signals.calculation_phase IS
    'A=14일 이동평균, B=DataLab 검색량, C=기본값(1.0) — 분석 근거 추적용';

CREATE INDEX idx_hiring_signals_stock_date
    ON hiring_signals (stock_id, observed_date DESC);

CREATE INDEX idx_hiring_signals_spike
    ON hiring_signals (observed_date DESC)
    WHERE is_spike = TRUE;

-- 기업별 공식 채용 사이트 크롤러 유형.
CREATE TYPE hiring_crawler_type AS ENUM (
    'portal_saramin',     -- 사람인 키워드 검색 (별도 처리, 이 테이블 불필요)
    'portal_jobkorea',    -- 잡코리아 키워드 검색 (별도 처리, 이 테이블 불필요)
    'official_api',       -- 공식 API 기반 (driver=None, 삼성전자)
    'official_selenium',  -- 공식 SPA/ATS Selenium (NAVER, 카카오, SK하이닉스 등)
    'recruiter_kr',       -- recruiter.co.kr 집계 (HL만도, 셀트리온, 유한양행)
    'simple_site'         -- 정적/CMS 사이트 (한미반도체, 스튜디오드래곤, 삼성바이오)
);

CREATE TABLE hiring_sources (
    id            BIGSERIAL PRIMARY KEY,
    stock_id      BIGINT NOT NULL REFERENCES stocks(id),
    crawler_type  hiring_crawler_type NOT NULL,
    crawler_class VARCHAR(100),      -- 사용할 크롤러 클래스명 (예: SamsungCrawler)
    base_url      VARCHAR(500),      -- 기업 공식 채용 페이지 루트 URL
    extra_config  JSONB,             -- CSS selector, API 파라미터 등 추가 설정
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 인라인 UNIQUE(자동이름 ..._stock_id_crawler_type_key) — 순차적용 016과 일치.
    UNIQUE (stock_id, crawler_type)
);

CREATE INDEX idx_hiring_sources_active
    ON hiring_sources (stock_id)
    WHERE is_active = TRUE;

-- 020: 직무(job function) 분류 + 종목별 직무 노출 가중치.
--   섹터 전반의 직무 수요를 peer 종목에서 전파해 own-momentum을 보완.
--   datalab_category_stocks 가중치 전파 패턴과 동일. 미시드 시 own-momentum로 폴백.
CREATE TABLE hiring_job_functions (
    id            BIGSERIAL    PRIMARY KEY,
    function_key  VARCHAR(40)  NOT NULL UNIQUE,   -- 안정 UPPERCASE 키, 예: 'ENGINEER'
    label         VARCHAR(100) NOT NULL,          -- 사람용 라벨, 예: '개발/엔지니어'
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE hiring_job_function_stocks (
    job_function_id BIGINT       NOT NULL REFERENCES hiring_job_functions(id) ON DELETE CASCADE,
    stock_id        BIGINT       NOT NULL REFERENCES stocks(id),
    weight          NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (job_function_id, stock_id)
);

CREATE INDEX idx_hiring_function_stocks_stock
    ON hiring_job_function_stocks (stock_id);


-- ============================================================================
-- Zone D — Processing/Normalization: 작업 큐 + 정규화 산출물
-- ============================================================================
-- processing_queue.stock_id는 NULL 허용 — DataLab 작업은 카테고리 단위로 인큐되고
-- Normalizer가 datalab_category_stocks로 카테고리 → 종목을 해석한다.

CREATE TABLE processing_queue (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    task_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'retrying', 'skipped')),
    priority VARCHAR(10) NOT NULL DEFAULT 'batch'
        CHECK (priority IN ('immediate', 'batch')),
    source_raw_ids BIGINT[],
    source_signal_event_ids BIGINT[],
    source_analysis_result_ids BIGINT[],
    task_context JSONB,
    retry_count SMALLINT NOT NULL DEFAULT 0,
    max_retry_count SMALLINT NOT NULL DEFAULT 3,
    error_message TEXT,
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_queue_pending
    ON processing_queue (task_type, priority, scheduled_at ASC)
    WHERE status IN ('pending', 'retrying');

CREATE INDEX idx_queue_immediate
    ON processing_queue (stock_id, scheduled_at ASC)
    WHERE priority = 'immediate' AND status = 'pending';

CREATE INDEX idx_queue_raw_ids
    ON processing_queue USING GIN (source_raw_ids);

CREATE INDEX idx_queue_signal_event_ids
    ON processing_queue USING GIN (source_signal_event_ids);

CREATE INDEX idx_queue_analysis_result_ids
    ON processing_queue USING GIN (source_analysis_result_ids);

CREATE TABLE source_documents (
    id BIGSERIAL PRIMARY KEY,
    raw_document_id BIGINT NOT NULL UNIQUE,
    stock_id BIGINT NOT NULL,
    source_type VARCHAR(20) NOT NULL
        CHECK (source_type IN ('DART', 'REPORT', 'HIRING', 'PATENT', 'DATALAB')),
    source_name VARCHAR(100) NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    reliability_level VARCHAR(10) NOT NULL DEFAULT 'medium'
        CHECK (reliability_level IN ('high', 'medium', 'low')),
    is_official BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_source_doc_stock
    ON source_documents (stock_id, source_type, published_at DESC);

CREATE TABLE signal_events (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id),
    event_hash VARCHAR(64) NOT NULL UNIQUE,
    source_type VARCHAR(20) NOT NULL
        CHECK (source_type IN ('DART', 'REPORT', 'HIRING', 'PATENT', 'DATALAB')),
    event_type VARCHAR(50) NOT NULL,
    event_date DATE NOT NULL,
    signal_direction VARCHAR(10) NOT NULL
        CHECK (signal_direction IN ('positive', 'negative', 'neutral', 'mixed', 'unknown')),
    impact_level VARCHAR(10) NOT NULL
        CHECK (impact_level IN ('high', 'medium', 'low')),
    title TEXT NOT NULL,
    summary TEXT,
    evidence_text TEXT,
    evidence_url TEXT,
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE signal_metrics (
    id BIGSERIAL PRIMARY KEY,
    signal_event_id BIGINT NOT NULL REFERENCES signal_events(id) ON DELETE CASCADE,
    metric_name VARCHAR(50) NOT NULL,
    metric_value NUMERIC(15,4) NOT NULL,
    metric_unit VARCHAR(20),
    previous_value NUMERIC(15,4),
    change_pct NUMERIC(8,2),
    period_start DATE,
    period_end DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_signal_metric UNIQUE (signal_event_id, metric_name)
);

CREATE TABLE validation_logs (
    id BIGSERIAL PRIMARY KEY,
    target_type VARCHAR(30) NOT NULL
        CHECK (target_type IN (
            'signal_event',
            'signal_metric',
            'analysis_result',
            'agent_result',
            'final_signal',
            'llm_output'
        )),
    target_id_int BIGINT,
    target_id_uuid UUID,
    validation_type VARCHAR(50) NOT NULL,
    passed BOOLEAN NOT NULL,
    message TEXT,
    retry_count SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_validation_target_id
        CHECK (num_nonnulls(target_id_int, target_id_uuid) = 1)
);


-- ============================================================================
-- Zone B — User/Billing 기본: 회원 + 구독 플랜 마스터
-- ============================================================================
-- analysis_requests(Zone E)가 users를 참조하므로 분석 Zone보다 먼저 생성한다.

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    member_code VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT,
    nickname VARCHAR(50),
    agreed_risk BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE subscription_plans (
    id BIGSERIAL PRIMARY KEY,
    plan_type VARCHAR(20) NOT NULL UNIQUE,
    plan_display_name VARCHAR(50) NOT NULL,
    max_watchlist INTEGER NOT NULL DEFAULT 3,
    signal_delay_hours INTEGER NOT NULL DEFAULT 24,
    journal_max_entries INTEGER NOT NULL DEFAULT 50,
    has_alt_data BOOLEAN NOT NULL DEFAULT FALSE,
    has_detail_report BOOLEAN NOT NULL DEFAULT FALSE,
    has_backtesting BOOLEAN NOT NULL DEFAULT FALSE,
    price_monthly INTEGER NOT NULL DEFAULT 0,
    price_yearly INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- Zone E — Analysis: 분석 요청 → 결과 → 점수 → 최종 시그널
-- ============================================================================
-- 분석 테이블 FK는 NO ACTION(기본) — 분석 이력은 원본 삭제로 연쇄 삭제하지 않는다.

CREATE TABLE analysis_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    analysis_mode VARCHAR(20) NOT NULL DEFAULT 'full'
        CHECK (analysis_mode IN ('full', 'dart_only', 'quick')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    ip_address INET
);

CREATE TABLE analysis_results (
    id BIGSERIAL PRIMARY KEY,
    request_id BIGINT REFERENCES analysis_requests(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    analysis_date DATE NOT NULL,
    run_key VARCHAR(30) NOT NULL DEFAULT 'BATCH',
    source_signal_event_ids BIGINT[] NOT NULL,
    base_score NUMERIC(5,2) NOT NULL CHECK (base_score BETWEEN 0 AND 100),
    pre_xgb_score NUMERIC(5,2),
    xgb_adj NUMERIC(5,2),
    analysis_mode VARCHAR(20) NOT NULL DEFAULT 'full'
        CHECK (analysis_mode IN ('full', 'dart_only', 'quick')),
    warning TEXT,
    disclaimer TEXT NOT NULL DEFAULT
        '본 서비스가 제공하는 시그널은 AI 에이전트의 데이터 분석 결과일 뿐, 투자 권유가 아니며 투자 손실에 대한 책임은 사용자에게 있습니다.',
    version VARCHAR(20) NOT NULL DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_analysis UNIQUE (
        stock_id,
        analysis_date,
        analysis_mode,
        run_key,
        version
    )
);

CREATE INDEX idx_analysis_stock_date
    ON analysis_results (stock_id, analysis_date DESC);

CREATE INDEX idx_analysis_run_key
    ON analysis_results (stock_id, analysis_date DESC, run_key);

CREATE INDEX idx_analysis_signal_events
    ON analysis_results USING GIN (source_signal_event_ids);

CREATE TABLE quant_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    score_breakdown JSONB NOT NULL,
    overall_score NUMERIC(5,2) NOT NULL,
    available_sources TEXT[] NOT NULL,
    missing_sources TEXT[],
    source_agreement VARCHAR(10) NOT NULL
        CHECK (source_agreement IN ('HIGH', 'MEDIUM', 'LOW')),
    failed_agent_count SMALLINT NOT NULL DEFAULT 0,
    alert_level SMALLINT NOT NULL DEFAULT 0
        CHECK (alert_level IN (0, 1, 2, 3)),
    score_cap_applied BOOLEAN NOT NULL DEFAULT FALSE,
    score_cap_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ta_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    ta_score NUMERIC(5,2),
    ta_detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ai_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    dart_agent_score NUMERIC(5,2),
    report_agent_score NUMERIC(5,2),
    alt_agent_score NUMERIC(5,2),
    dart_confidence NUMERIC(5,2),
    report_confidence NUMERIC(5,2),
    alt_confidence NUMERIC(5,2),
    validation_log JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agent_results (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    debate_method VARCHAR(5) NOT NULL
        CHECK (debate_method IN ('D-1', 'D-2', 'D-3', 'D-4', 'D-5')),
    source_signal_event_ids BIGINT[],
    method_score NUMERIC(5,2) NOT NULL CHECK (method_score BETWEEN 0 AND 100),
    method_signal VARCHAR(10) NOT NULL
        CHECK (method_signal IN ('positive', 'negative', 'neutral', 'mixed')),
    method_detail JSONB NOT NULL,
    reliability_score NUMERIC(5,2),
    evidence_quality NUMERIC(5,2),
    llm_model VARCHAR(50),
    prompt_ver VARCHAR(20) DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_result_method UNIQUE (result_id, debate_method)
);

CREATE INDEX idx_agent_result_id
    ON agent_results (result_id);

CREATE INDEX idx_agent_method
    ON agent_results (result_id, debate_method);

CREATE INDEX idx_agent_source_signal_event_ids
    ON agent_results USING GIN (source_signal_event_ids);

CREATE TABLE xgb_model_versions (
    id BIGSERIAL PRIMARY KEY,
    model_version VARCHAR(20) NOT NULL UNIQUE,
    trained_at TIMESTAMPTZ,
    feature_names JSONB,
    feature_importance JSONB,
    validation_score NUMERIC(5,2),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    training_samples INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_xgb_active
    ON xgb_model_versions (is_active)
    WHERE is_active = TRUE;

CREATE TABLE ml_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    model_version_id BIGINT REFERENCES xgb_model_versions(id),
    ml_score NUMERIC(5,2) NOT NULL,
    calibrated_score NUMERIC(5,2) NOT NULL CHECK (calibrated_score BETWEEN 0 AND 100),
    prediction_label VARCHAR(20),
    feature_importance JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE final_signals (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    analysis_result_id BIGINT NOT NULL REFERENCES analysis_results(id),
    signal_date DATE NOT NULL,
    run_key VARCHAR(30) NOT NULL DEFAULT 'BATCH',
    version VARCHAR(20) NOT NULL DEFAULT '1.0',
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    final_score NUMERIC(5,2) NOT NULL CHECK (final_score BETWEEN 0 AND 100),
    confidence NUMERIC(5,2) NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    signal VARCHAR(10) NOT NULL
        CHECK (signal IN ('positive', 'negative', 'neutral', 'mixed')),
    source_agreement VARCHAR(10) NOT NULL
        CHECK (source_agreement IN ('HIGH', 'MEDIUM', 'LOW')),
    warning_level VARCHAR(10) NOT NULL DEFAULT 'NORMAL'
        CHECK (warning_level IN ('NORMAL', 'CAUTION', 'WARNING')),
    score_breakdown JSONB NOT NULL,
    summary TEXT NOT NULL,
    bull_point TEXT,
    bear_point TEXT,
    disclaimer TEXT NOT NULL DEFAULT
        '본 서비스가 제공하는 시그널은 AI 에이전트의 데이터 분석 결과일 뿐, 투자 권유가 아니며 투자 손실에 대한 책임은 사용자에게 있습니다.',
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    min_plan_required VARCHAR(20) NOT NULL DEFAULT 'free'
        CHECK (min_plan_required IN ('free', 'pro', 'premium')),
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 017: Alternative consensus 출력 (alignment_rate는 source_agreement 별칭이라 미저장).
    consensus_score NUMERIC(5,2) CHECK (consensus_score BETWEEN 0 AND 100),
    positive_evidence JSONB,
    caution_evidence JSONB,
    CONSTRAINT uq_final_signal_version UNIQUE (
        stock_id,
        signal_date,
        run_key,
        version
    ),
    CONSTRAINT chk_final_signal_publish_time
        CHECK (
            is_published = FALSE
            OR published_at IS NOT NULL
        )
);

CREATE UNIQUE INDEX uq_final_signal_current
    ON final_signals (stock_id, signal_date, run_key)
    WHERE is_current = TRUE;

CREATE INDEX idx_final_stock_date
    ON final_signals (stock_id, signal_date DESC);

CREATE INDEX idx_final_run_key
    ON final_signals (stock_id, signal_date DESC, run_key);

CREATE INDEX idx_final_published
    ON final_signals (is_published, published_at DESC)
    WHERE is_published = TRUE;

CREATE TABLE score_history (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    final_signal_id BIGINT REFERENCES final_signals(id),
    analysis_result_id BIGINT REFERENCES analysis_results(id),
    signal_date DATE NOT NULL,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    final_score NUMERIC(5,2) NOT NULL,
    pre_xgb_score NUMERIC(5,2),
    reliability_score NUMERIC(5,2),
    model_version VARCHAR(20),
    scoring_version VARCHAR(20),
    reanalysis_reason VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_score_history_reference
        CHECK (
            final_signal_id IS NOT NULL
            OR analysis_result_id IS NOT NULL
        )
);

CREATE INDEX idx_score_history_stock
    ON score_history (stock_id, signal_date DESC, scored_at DESC);

CREATE INDEX idx_score_history_final_signal
    ON score_history (final_signal_id)
    WHERE final_signal_id IS NOT NULL;

CREATE TABLE backtest_results (
    id BIGSERIAL PRIMARY KEY,
    final_signal_id BIGINT NOT NULL REFERENCES final_signals(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    signal_date DATE NOT NULL,
    signal_score NUMERIC(5,2) NOT NULL,
    signal_value VARCHAR(10) NOT NULL,
    price_at_signal NUMERIC(12,2) NOT NULL,
    price_after_5d NUMERIC(12,2),
    change_pct_5d NUMERIC(6,2),
    checked_at TIMESTAMPTZ,
    is_hit BOOLEAN,
    confidence_band VARCHAR(15),
    source_agreement VARCHAR(10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- Zone F — User/Billing 확장: 구독·워치리스트·저널·본인인증·약관
-- ============================================================================
-- signal_journals / user_signal_reads가 final_signals(Zone E)를 참조하므로 뒤에 위치.

CREATE TABLE signal_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    plan_id BIGINT NOT NULL REFERENCES subscription_plans(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'cancelled', 'trial')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    payment_method VARCHAR(50),
    billing_cycle VARCHAR(10)
        CHECK (
            billing_cycle IN ('monthly', 'yearly')
            OR billing_cycle IS NULL
        ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_subscription_active
    ON signal_subscriptions (user_id)
    WHERE status = 'active';

CREATE TABLE watchlists (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    notification_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_watchlist UNIQUE (user_id, stock_id)
);

CREATE INDEX idx_watchlists_user
    ON watchlists (user_id);

CREATE INDEX idx_watchlists_stock
    ON watchlists (stock_id);

CREATE TABLE signal_journals (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    final_signal_id BIGINT REFERENCES final_signals(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    user_view VARCHAR(20) NOT NULL
        CHECK (user_view IN ('watch', 'bullish', 'bearish', 'neutral')),
    user_memo TEXT,
    decision_type VARCHAR(20),
    decision_reason TEXT,
    signal_score_at_time NUMERIC(5,2),
    signal_value_at_time VARCHAR(10),
    price_at_time NUMERIC(12,2),
    source_agreement_at_time VARCHAR(10),
    outcome_price NUMERIC(12,2),
    outcome_change_pct NUMERIC(6,2),
    outcome_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signal_journals_user_created
    ON signal_journals (user_id, created_at DESC);

CREATE INDEX idx_signal_journals_stock_created
    ON signal_journals (stock_id, created_at DESC);

CREATE INDEX idx_signal_journals_final_signal
    ON signal_journals (final_signal_id)
    WHERE final_signal_id IS NOT NULL;

CREATE TABLE user_signal_reads (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    final_signal_id BIGINT NOT NULL REFERENCES final_signals(id),
    read_date DATE NOT NULL DEFAULT CURRENT_DATE,
    read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_read UNIQUE (user_id, final_signal_id)
);

CREATE INDEX idx_user_signal_reads_user_read_at
    ON user_signal_reads (user_id, read_at DESC);

CREATE INDEX idx_user_signal_reads_final_signal
    ON user_signal_reads (final_signal_id);

CREATE TABLE social_accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('google', 'kakao', 'naver')),
    provider_user_id VARCHAR(100) NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_social UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_social_accounts_user
    ON social_accounts (user_id);

CREATE TABLE portone_verifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    imp_uid VARCHAR(100) NOT NULL UNIQUE,
    merchant_uid VARCHAR(100) NOT NULL,
    verification_type VARCHAR(20) CHECK (verification_type IN ('identity', 'payment')),
    status VARCHAR(20) NOT NULL,
    verified_at TIMESTAMPTZ,
    raw_response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_portone_verifications_user_created
    ON portone_verifications (user_id, created_at DESC);

CREATE INDEX idx_portone_verifications_merchant_uid
    ON portone_verifications (merchant_uid);

CREATE TABLE terms_agreements (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    terms_type VARCHAR(50) NOT NULL,
    version VARCHAR(20) NOT NULL,
    agreed BOOLEAN NOT NULL DEFAULT TRUE,
    agreed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address INET,
    CONSTRAINT uq_terms_agreement UNIQUE (user_id, terms_type, version)
);

CREATE INDEX idx_terms_agreements_user_agreed_at
    ON terms_agreements (user_id, agreed_at DESC);


-- ============================================================================
-- Zone G — Admin: 관리자 계정 + 세션
-- ============================================================================

CREATE TABLE admin_accounts (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_admin_accounts_email
    ON admin_accounts (email);

CREATE INDEX idx_admin_accounts_is_active
    ON admin_accounts (is_active);

CREATE TABLE admin_sessions (
    id BIGSERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL REFERENCES admin_accounts(id),
    session_token VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    last_activity_at TIMESTAMPTZ,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_admin_sessions_admin
    ON admin_sessions (admin_id);

CREATE INDEX idx_admin_sessions_expires_at
    ON admin_sessions (expires_at);

CREATE INDEX idx_admin_sessions_token
    ON admin_sessions (session_token);


-- ============================================================================
-- Triggers: updated_at 자동 갱신 + final_signals.is_current 단일화
-- ============================================================================
-- 규칙: updated_at 컬럼이 있는 모든 테이블에는 trg_<table>_updated_at 트리거를 부착.

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_stocks_updated_at
BEFORE UPDATE ON stocks
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_dart_corp_codes_updated_at
BEFORE UPDATE ON dart_corp_codes
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_dart_collection_states_updated_at
BEFORE UPDATE ON dart_collection_states
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_datalab_categories_updated_at
BEFORE UPDATE ON datalab_categories
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_datalab_category_keywords_updated_at
BEFORE UPDATE ON datalab_category_keywords
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_hiring_baseline_updated_at
BEFORE UPDATE ON hiring_baseline
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

-- 020: hiring_job_functions.updated_at
CREATE TRIGGER trg_hiring_job_functions_updated_at
BEFORE UPDATE ON hiring_job_functions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_processing_queue_updated_at
BEFORE UPDATE ON processing_queue
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_signal_subscriptions_updated_at
BEFORE UPDATE ON signal_subscriptions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_signal_journals_updated_at
BEFORE UPDATE ON signal_journals
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE FUNCTION set_final_signal_current()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current = TRUE THEN
        PERFORM pg_advisory_xact_lock(
            hashtext(NEW.stock_id::text || '|' || NEW.signal_date::text || '|' || NEW.run_key)
        );

        UPDATE final_signals
        SET is_current = FALSE
        WHERE stock_id = NEW.stock_id
          AND signal_date = NEW.signal_date
          AND run_key = NEW.run_key
          AND is_current = TRUE
          AND id IS DISTINCT FROM NEW.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_final_signal_current
BEFORE INSERT OR UPDATE OF is_current, stock_id, signal_date, run_key
ON final_signals
FOR EACH ROW
EXECUTE FUNCTION set_final_signal_current();


-- ============================================================================
-- Legacy — report MVP ⚠️ 폐기 예정
-- ============================================================================
-- report_raw / report_signal은 setup_db.py(삭제됨)가 마이그레이션 체계 밖에서
-- 만들던 테이블로, 현재 report 파이프라인 코드가 아직 사용 중이라 보존한다:
--   - services/agent-worker/app/collectors/report/collector.py  (report_raw SELECT)
--   - services/agent-worker/app/collectors/report/parsers/vector_store.py  (report_raw INSERT/백필)
--   - services/agent-worker/app/analyzers/report/analyzer.py  (report_signal INSERT)
-- 이전 계획: report 파이프라인을 공용 스키마(raw_documents → report_raw_details)로
-- 이전한 뒤 이 테이블들을 DROP한다. 새 코드에서 참조 금지.

CREATE TABLE report_raw (
    id                BIGSERIAL PRIMARY KEY,
    stock_code        VARCHAR(10)  NOT NULL,
    firm              VARCHAR(50)  NOT NULL,
    date              VARCHAR(20)  NOT NULL,
    report_type       VARCHAR(30),
    title             TEXT,
    pdf_url           TEXT,
    target_price      INT,
    opinion           VARCHAR(20),
    key_rationale     TEXT,
    raw_text_preview  TEXT,
    processed         BOOLEAN      DEFAULT FALSE,
    created_at        TIMESTAMPTZ  DEFAULT NOW(),
    CONSTRAINT uq_report_raw UNIQUE (firm, date, stock_code)
);

CREATE INDEX idx_report_raw_stock
    ON report_raw (stock_code);

CREATE TABLE report_signal (
    id                BIGSERIAL PRIMARY KEY,
    stock_code        VARCHAR(10)  NOT NULL,
    direction         VARCHAR(20),
    score             FLOAT,
    avg_target        FLOAT,
    upside_pct        FLOAT,
    target_trend      VARCHAR(20),
    conflict_detected BOOLEAN,
    opinions          JSONB,
    risk_flags        JSONB,
    summary           TEXT,
    data_status       VARCHAR(20),
    analyzed_at       TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_report_signal_stock
    ON report_signal (stock_code);
