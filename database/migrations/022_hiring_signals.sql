-- 022_hiring_signals.sql
-- HiringAnalyzer 분석 결과 저장 테이블

CREATE TABLE IF NOT EXISTS hiring_signals (
    id                BIGSERIAL PRIMARY KEY,
    stock_id          BIGINT NOT NULL REFERENCES stocks(id),
    observed_date     DATE NOT NULL,
    job_count         INTEGER NOT NULL DEFAULT 0,
    baseline          NUMERIC(10,2),
    relative_strength NUMERIC(8,4),
    is_spike          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (stock_id, observed_date)
);

CREATE INDEX IF NOT EXISTS idx_hiring_signals_stock_date
    ON hiring_signals (stock_id, observed_date DESC);

CREATE INDEX IF NOT EXISTS idx_hiring_signals_spike
    ON hiring_signals (observed_date DESC)
    WHERE is_spike = TRUE;
