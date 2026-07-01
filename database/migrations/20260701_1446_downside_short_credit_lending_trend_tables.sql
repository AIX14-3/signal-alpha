-- 20260701_1446_downside_short_credit_lending_trend_tables.sql
-- target: collection
-- ============================================================================
-- downside short credit lending trend tables
-- ----------------------------------------------------------------------------
-- 배경:
--   현 파이프라인은 상방 편향(방향 근거가 사실상 주가 기술지표뿐, 대체데이터는
--   성장/수요 감지라 롱 편향). 하락을 대칭적으로 잡기 위해 포지셔닝/레버리지
--   스트레스 데이터(공매도·신용·대차)를 키움 REST(조회 TR)로 수집·적재한다.
--   프로그램매매/환율은 기존 program_trading/fx_rates 테이블을 재사용한다.
-- 설계:
--   종목·일자(stock_id, trade_date) 단위 일별 추이. 수치는 DB 저장값만 사용
--   (LLM 미개입). 키움 원필드 → 컬럼 매핑은 각 테이블 주석 참조.
-- ============================================================================

-- 작성 규칙: 적용 여부는 schema_migrations 원장이 관리하므로 IF NOT EXISTS를 쓰지 않는다.
-- 시드 데이터가 필요하면 seeds/에 분리하고 ON CONFLICT로 재실행 가능하게 작성한다.
-- 적용 후에는 이 파일을 수정하지 말 것(checksum 검증). 변경은 새 마이그레이션으로 추가한다.

-- ----------------------------------------------------------------------------
-- 공매도추이 (키움 ka10014 / shrts_trnsn)
-- ----------------------------------------------------------------------------
CREATE TABLE public.short_selling_trend (
    id               bigserial      PRIMARY KEY,
    stock_id         bigint NOT NULL REFERENCES public.stocks(id) ON DELETE CASCADE,
    trade_date       date   NOT NULL,
    close_price      bigint,           -- close_pric 종가
    change_rate      numeric(8,2),     -- flu_rt 등락률
    volume           bigint,           -- trde_qty 거래량
    short_volume     bigint,           -- shrts_qty 공매도량
    short_weight_pct numeric(8,2),     -- trde_wght 공매도 매매비중(%)
    short_value_krw  bigint,           -- shrts_trde_prica 공매도 거래대금(천원)
    short_avg_price  bigint,           -- shrts_avg_pric 공매도 평균가
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_short_selling_trend_stock_date UNIQUE (stock_id, trade_date)
);
CREATE INDEX idx_short_selling_trend_date ON public.short_selling_trend (trade_date);

-- ----------------------------------------------------------------------------
-- 신용매매동향 (키움 ka10013 / crd_trde_trend)
-- ----------------------------------------------------------------------------
CREATE TABLE public.credit_trade_trend (
    id             bigserial      PRIMARY KEY,
    stock_id       bigint NOT NULL REFERENCES public.stocks(id) ON DELETE CASCADE,
    trade_date     date   NOT NULL,
    cur_price      bigint,             -- cur_prc 현재가
    volume         bigint,             -- trde_qty 거래량
    credit_new     bigint,             -- new 신용신규
    credit_repay   bigint,             -- rpya 신용상환
    credit_balance bigint,             -- remn 신용융자 잔고
    credit_amount  bigint,             -- amt 융자금액
    credit_net     bigint,             -- pre 순증(신규-상환)
    balance_ratio  numeric(8,2),       -- remn_rt 잔고율
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_credit_trade_trend_stock_date UNIQUE (stock_id, trade_date)
);
CREATE INDEX idx_credit_trade_trend_date ON public.credit_trade_trend (trade_date);

-- ----------------------------------------------------------------------------
-- 대차거래추이 (키움 ka20068 / dbrt_trde_trnsn) — 공매도 선행지표
-- ----------------------------------------------------------------------------
CREATE TABLE public.securities_lending_trend (
    id                     bigserial      PRIMARY KEY,
    stock_id               bigint NOT NULL REFERENCES public.stocks(id) ON DELETE CASCADE,
    trade_date             date   NOT NULL,
    lending_contract       bigint,     -- dbrt_trde_cntrcnt 대차 체결주수
    lending_repay          bigint,     -- dbrt_trde_rpy 대차 상환주수
    lending_change         bigint,     -- dbrt_trde_irds 증감(체결-상환)
    lending_balance        bigint,     -- rmnd 대차잔고 주수
    lending_balance_amount bigint,     -- remn_amt 대차잔고 금액
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_securities_lending_trend_stock_date UNIQUE (stock_id, trade_date)
);
CREATE INDEX idx_securities_lending_trend_date ON public.securities_lending_trend (trade_date);
