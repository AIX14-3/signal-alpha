-- 20260702_2300_journal_chart_prices.sql
-- target: backend
-- ============================================================================
-- 저널 차트용 종가 시리즈 (워커→백엔드 동기화 사본)
-- ----------------------------------------------------------------------------
-- 배경: 저널 상세에서 "작성 시점 기준으로 올랐는지/내렸는지"를 주가 차트로 보여준다.
--   일봉(ohlcv_data)은 수집 DB 에만 있으므로, outcome 러너(run_journal_outcomes)가
--   저널이 있는 종목의 종가 시리즈(작성일 30일 전 ~ 최신)를 이 테이블로 매일 멱등
--   upsert 한다 — signal_journal_outcomes 와 동일한 워커→백엔드 계약.
-- 설계: 종목×거래일 1행(PK). 저널별 복제가 아니라 종목 단위라 같은 종목의 저널이
--   많아도 시리즈는 1벌. close 만 복사한다(차트는 종가 라인만 필요 — OHLC 전체 이중화
--   방지). FK 는 backend 의 stocks 발행 사본을 참조.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.signal_journal_chart_prices (
    stock_id    bigint NOT NULL REFERENCES public.stocks(id),
    trade_date  date NOT NULL,
    close_price numeric(12,2) NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id, trade_date)
);

-- Grants (멱등, 롤 존재 시에만):
--   signal_backend : 저널 차트 API 읽기 전용.
--   signal_worker  : outcome 러너가 시리즈 동기화(INSERT + upsert UPDATE).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT ON public.signal_journal_chart_prices TO signal_backend;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT, INSERT, UPDATE ON public.signal_journal_chart_prices TO signal_worker;
    END IF;
END $$;
