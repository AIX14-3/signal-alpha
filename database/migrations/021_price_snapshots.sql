-- 키움 REST API 실시간(장중 폴링) 주가 스냅샷.
-- kiwoom_data_spec의 OPT10001/ka10001(주식 기본 정보) 필드와 1:1 대응.
-- 일봉 확정치는 기존 ohlcv_data가 담당하고, 이 테이블은 장중 시점별 관측값을 보관한다.

CREATE TABLE IF NOT EXISTS price_snapshots (
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

CREATE INDEX IF NOT EXISTS idx_price_snapshots_stock_time
    ON price_snapshots (stock_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_snapshots_trade_date
    ON price_snapshots (trade_date);
