-- 020_hiring_job_functions.sql
--
-- The Hiring analyzer currently scores only a company's OWN job-posting momentum.
-- C4 adds a second dimension: sector-wide demand for the JOB FUNCTIONS a company
-- depends on (engineers, manufacturing, sales…), propagated from peer companies.
-- A surge in sector-wide engineer hiring is a forward demand signal for a stock
-- whose output depends on engineers, even before its own postings move.
--
-- This mirrors the datalab_category_stocks weight-propagation pattern:
--   hiring_job_functions          — the canonical function taxonomy (ENGINEER, SALES…)
--   hiring_job_function_stocks    — stock ↔ function exposure weights (how much a
--                                   stock depends on each function)
--
-- Postings are classified to a function in code (the loader), NOT here, so no
-- collector change / re-collection is needed (see app/analyzers/hiring/job_functions.py).
-- Both tables are optional for the analyzer: when unseeded it falls back to the
-- pure own-momentum score, so this migration is safe to apply before seeding.

CREATE TABLE hiring_job_functions (
    id            BIGSERIAL    PRIMARY KEY,
    function_key  VARCHAR(40)  NOT NULL UNIQUE,   -- stable UPPERCASE key, e.g. 'ENGINEER'
    label         VARCHAR(100) NOT NULL,          -- human label, e.g. '개발/엔지니어'
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- updated_at 컬럼 → 012 공통 컨벤션대로 트리거 부착 (update_updated_at()는 012에서 정의됨).
CREATE TRIGGER trg_hiring_job_functions_updated_at
BEFORE UPDATE ON hiring_job_functions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TABLE hiring_job_function_stocks (
    job_function_id BIGINT       NOT NULL REFERENCES hiring_job_functions(id) ON DELETE CASCADE,
    stock_id        BIGINT       NOT NULL REFERENCES stocks(id),
    weight          NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (job_function_id, stock_id)
);

CREATE INDEX idx_hiring_function_stocks_stock
    ON hiring_job_function_stocks (stock_id);
