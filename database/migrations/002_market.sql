CREATE TABLE IF NOT EXISTS stocks (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    market VARCHAR(10) NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ')),
    sector VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ohlcv_data (
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

CREATE INDEX IF NOT EXISTS idx_ohlcv_stock_date
    ON ohlcv_data (stock_id, trade_date DESC);

CREATE TABLE IF NOT EXISTS fundamentals (
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
