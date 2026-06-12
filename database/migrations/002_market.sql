-- 002_market.sql
-- Zone A (Market): 종목 마스터 + 시세 데이터.
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
