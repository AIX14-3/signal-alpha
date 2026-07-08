-- 20260708_1400_stock_price_daily.sql
-- target: backend
-- ============================================================================
-- 종목별 일봉 종가 시리즈 (워커→백엔드 동기화 사본) — 공개 홈 차트용
-- ----------------------------------------------------------------------------
-- 배경: 메인 홈 v2 "실시간 분석 종목" 인라인 아코디언에서 종목 가격 차트를 보여준다.
--   일봉(ohlcv_data)은 수집 DB 에만 있으므로, 발행 러너(sync_stock_prices)가 분석 종목
--   전체의 종가 시리즈를 이 테이블로 매일 멱등 upsert 한다 — signal_journal_chart_prices /
--   signal_journal_outcomes 와 동일한 워커→백엔드 계약(백엔드는 수집 DB 에 접속하지 않음).
-- 설계: 종목×거래일 1행(PK). close 만 복사한다(차트는 종가 라인만 필요 — OHLC 전체 이중화
--   방지). 저널 종속인 signal_journal_chart_prices 와 달리 저널 유무와 무관하게 분석 종목
--   전체를 커버한다. FK 는 backend 의 stocks 발행 사본을 참조.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.stock_price_daily (
    stock_id    bigint NOT NULL REFERENCES public.stocks(id),
    trade_date  date NOT NULL,
    close_price numeric(12,2) NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id, trade_date)
);

-- Grants (멱등, 롤 존재 시에만):
--   signal_backend : 공개 가격 차트 API 읽기 전용(FROM stock_price_daily 직접 SELECT).
--   signal_worker  : 발행 러너가 시리즈 동기화(INSERT + upsert UPDATE).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT ON public.stock_price_daily TO signal_backend;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT, INSERT, UPDATE ON public.stock_price_daily TO signal_worker;
    END IF;
END $$;
