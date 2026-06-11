-- 013_legacy_report_mvp.sql
--
-- ⚠️ DEPRECATED — report RAG MVP 전용 레거시 테이블.
--
-- report_raw / report_signal은 setup_db.py(삭제됨)가 마이그레이션 체계 밖에서
-- 만들던 테이블로, 현재 report 파이프라인 코드가 아직 사용 중이라 베이스라인에 편입한다:
--   - app/collectors/report/collector.py  (report_raw SELECT)
--   - app/collectors/report/parsers/vector_store.py  (report_raw INSERT/백필)
--   - app/analyzers/report/analyzer.py  (report_signal INSERT)
--
-- 이전 계획: report 파이프라인을 공용 스키마(raw_documents → report_raw_details →
-- report_chunks)로 이전한 뒤 이 테이블들을 별도 마이그레이션으로 DROP한다.
-- 새 코드에서 이 테이블을 참조하지 말 것.
--
-- 원본 대비 정규화: SERIAL → BIGSERIAL, TIMESTAMP → TIMESTAMPTZ.
-- (date VARCHAR(20) 등 나머지 형태는 기존 코드가 의존하므로 유지)

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
