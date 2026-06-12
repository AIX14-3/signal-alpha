-- 003_collection_core.sql
-- Zone C (Collection): 수집 실행 이력 + 종목 단위 원본 문서/상세.
-- raw_documents의 uq_raw_document_stock UNIQUE(id, stock_id)는 detail 테이블의
-- 복합 FK 대상이다 (detail 행의 stock_id가 원본 문서와 일치함을 DB 차원에서 보장).
-- detail 테이블의 ON DELETE CASCADE: 원본 문서 삭제 시 상세도 함께 삭제.
-- report_chunks: 리포트 PDF 청크 + pgvector 임베딩 (RAG 검색용, canonical 스키마).

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
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_hiring_stock_keyword
    ON hiring_raw_details (stock_id, keyword);

CREATE INDEX idx_hiring_change
    ON hiring_raw_details (stock_id, change_pct DESC);

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

CREATE TABLE report_chunks (
    id BIGSERIAL PRIMARY KEY,
    raw_document_id BIGINT NOT NULL,
    stock_id BIGINT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    token_count INTEGER,
    embedding VECTOR(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_chunk UNIQUE (raw_document_id, chunk_index),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_chunks_stock
    ON report_chunks (stock_id);

CREATE INDEX idx_chunks_embedding
    ON report_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
