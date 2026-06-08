-- 업종(섹터) 지수 도메인.
-- 업종은 종목과 차원이 달라 ohlcv_data 를 재사용하지 않고 전용 테이블로 둔다.
-- 코스피/코스닥 종합지수는 is_market_index=TRUE 인 sectors 행으로 통합 관리하여
-- 상대강도 분모(시장 수익률)를 동일 파이프라인으로 수집한다.

CREATE TABLE IF NOT EXISTS sectors (
    id BIGSERIAL PRIMARY KEY,
    kiwoom_code VARCHAR(10) NOT NULL,
    market VARCHAR(10) NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ')),
    name VARCHAR(100) NOT NULL,
    is_market_index BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_sector UNIQUE (market, kiwoom_code)
);

CREATE INDEX IF NOT EXISTS idx_sectors_market_index
    ON sectors (market)
    WHERE is_market_index = TRUE;

CREATE TABLE IF NOT EXISTS sector_ohlcv (
    id BIGSERIAL PRIMARY KEY,
    sector_id BIGINT NOT NULL REFERENCES sectors(id),
    trade_date DATE NOT NULL,
    open NUMERIC(12,2) NOT NULL,
    high NUMERIC(12,2) NOT NULL,
    low NUMERIC(12,2) NOT NULL,
    close NUMERIC(12,2) NOT NULL,          -- 업종지수 종가 (pt)
    volume BIGINT,                          -- 업종 전체 거래량 (주)
    trading_value BIGINT,                   -- 업종 전체 거래대금 (백만원)
    change_pct NUMERIC(6,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_sector_ohlcv UNIQUE (sector_id, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_sector_ohlcv_sector_date
    ON sector_ohlcv (sector_id, trade_date DESC);

-- 종목 ↔ 업종 구조적 연결. 기존 stocks.sector(자유 텍스트)는 표시용으로 유지.
ALTER TABLE stocks
    ADD COLUMN IF NOT EXISTS sector_id BIGINT REFERENCES sectors(id);

CREATE INDEX IF NOT EXISTS idx_stocks_sector
    ON stocks (sector_id)
    WHERE sector_id IS NOT NULL;

-- updated_at 자동 갱신 (009_triggers.sql 의 update_updated_at 재사용)
DROP TRIGGER IF EXISTS trg_sectors_updated_at ON sectors;
CREATE TRIGGER trg_sectors_updated_at
BEFORE UPDATE ON sectors
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();
