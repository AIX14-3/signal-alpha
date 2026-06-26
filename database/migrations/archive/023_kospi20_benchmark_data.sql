-- 023_kospi20_benchmark_data.sql
-- ============================================================================
-- KOSPI20 벤치마크 데이터 소스 적재 스키마 — 수급(투자자·프로그램)·환율
-- ----------------------------------------------------------------------------
-- 배경
--   vol-benchmark의 KOSPI 시총 상위 20종목 × 3년치(2023-06~2026-06) 수집본
--   (docs benchmark-kospi20)을 signal-alpha 파이프라인 DB로 가져오기 위한 스키마.
--   OHLCV + 외국인/기관 순매수는 기존 ohlcv_data에 들어가나, 개인 순매수·프로그램매매·
--   환율은 담을 곳이 없어 추가한다. ML/DL(변동성) 추론이 실제 데이터를 보게 하는 것이 목적.
--
-- 설계
--   - ohlcv_data.individual_net 추가 → 외국인/기관/개인 순매수를 한 행(종목·일)에 모은다.
--   - program_trading: 키움 ka90004 종목별 프로그램매매(매수/매도/순수량/순금액). 종목·일 멱등.
--   - fx_rates: 토스 USD/KRW 환율(시장 공통 매크로). pair·일 멱등(종목 무관).
--   - 수급은 '레벨'이 아니라 회전율/로그수익률로 피처화(가공은 벤치마크 build_altfeat 참조).
-- ============================================================================

ALTER TABLE ohlcv_data
    ADD COLUMN IF NOT EXISTS individual_net BIGINT;

CREATE TABLE program_trading (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    trade_date DATE NOT NULL,
    prog_buy_qty BIGINT,
    prog_sell_qty BIGINT,
    prog_net_qty BIGINT,
    prog_net_amt BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_program_trading UNIQUE (stock_id, trade_date)
);

CREATE INDEX idx_program_trading_stock_date
    ON program_trading (stock_id, trade_date);

CREATE TABLE fx_rates (
    id BIGSERIAL PRIMARY KEY,
    pair VARCHAR(16) NOT NULL DEFAULT 'USD/KRW',
    trade_date DATE NOT NULL,
    rate DOUBLE PRECISION,
    mid DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fx_rates UNIQUE (pair, trade_date)
);

CREATE INDEX idx_fx_rates_date
    ON fx_rates (trade_date);
