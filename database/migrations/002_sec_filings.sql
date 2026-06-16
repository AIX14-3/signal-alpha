-- 002_sec_filings.sql
-- ============================================================================
-- 해외(미국) 공시 수집 — SEC EDGAR 공시 목록 적재
-- ----------------------------------------------------------------------------
-- DART(dart_corp_codes / dart_raw_details)에 대응하는 미국판 테이블.
-- submissions API의 공시 목록(form/제출일/accession/원문문서)을 적재한다.
-- accession_no(예: 0001045810-26-000052)를 자연키로 멱등 upsert 한다.
-- 본문 파싱·정형 재무(XBRL)·신호 emit은 후속 마이그레이션/단계에서 추가한다.
-- ============================================================================

CREATE TABLE sec_filings (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    cik VARCHAR(10) NOT NULL,                       -- zero-padded 10자리 (예: 0001045810)
    ticker VARCHAR(20) NOT NULL,
    company_name VARCHAR(255),
    form VARCHAR(20) NOT NULL,                       -- 8-K / 10-K / 10-Q / 4 / SC 13D ...
    filing_date DATE NOT NULL,
    accession_no VARCHAR(25) NOT NULL UNIQUE,        -- 자연키
    primary_document VARCHAR(255),
    primary_doc_description VARCHAR(255),
    document_url TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sec_filings_stock
    ON sec_filings (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX idx_sec_filings_ticker
    ON sec_filings (ticker);

CREATE INDEX idx_sec_filings_cik_form
    ON sec_filings (cik, form);

CREATE INDEX idx_sec_filings_filing_date
    ON sec_filings (filing_date DESC);

-- updated_at 자동 갱신 트리거 (database/README §3 컨벤션: updated_at 컬럼 → trg_<table>_updated_at)
CREATE TRIGGER trg_sec_filings_updated_at
BEFORE UPDATE ON sec_filings
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();
