-- 004_dart_financial_facts.sql
-- ============================================================================
-- L1 정형 재무 적재 — OpenDART fnlttSinglAcntAll(단일회사 전체 재무제표)
-- ----------------------------------------------------------------------------
-- 기존 analyzers/dart/financials.py 는 공시 본문 텍스트를 정규식으로 긁어
-- 매출·영업이익·순이익만 뽑았다(부분 구현). 이 테이블은 표준계정ID(account_id)
-- 기반의 다기간 재무 시계열을 표준계정 단위로 적재한다.
--
-- 멱등 자연키: (corp_code, bsns_year, reprt_code, fs_div, sj_div,
--              COALESCE(account_id, account_nm)) — account_id 가 비어있는
-- 비표준 항목은 account_nm 으로 폴백한다. 같은 (회사,연도,보고서)에 rcept_no 가
-- 갱신되면(정정) 최신 rcept_no 행으로 덮어쓴다.
--
-- ★4 파생지표(YoY/부채비율/회전율 등)·신호 emit 은 분석 단계에서 추가한다.
-- ============================================================================

CREATE TABLE dart_financial_facts (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL,
    rcept_no VARCHAR(30) NOT NULL,              -- 접수번호(정정 추적 키)
    bsns_year SMALLINT NOT NULL,
    reprt_code VARCHAR(5) NOT NULL,             -- 11011=사업/11012=반기/11013=1Q/11014=3Q
    fs_div VARCHAR(3) NOT NULL,                 -- CFS=연결 / OFS=별도
    sj_div VARCHAR(5) NOT NULL,                 -- BS/IS/CIS/CF/SCE
    account_id VARCHAR(100),                    -- 표준계정ID(없을 수 있음)
    account_nm VARCHAR(200) NOT NULL,
    amount_krw NUMERIC(24,0),                   -- 원 단위 정규화 금액(당기)
    amount_raw TEXT,                            -- 원본 문자열(검증용)
    currency VARCHAR(10) NOT NULL DEFAULT 'KRW',
    period_label VARCHAR(10) NOT NULL,          -- 2025FY / 2025Q3 등
    fiscal_period VARCHAR(10),                  -- annual / cumulative
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 멱등 자연키(account_id 빈 값은 account_nm 폴백). ON CONFLICT 추론 대상.
CREATE UNIQUE INDEX uq_dart_fin_fact
    ON dart_financial_facts (
        corp_code, bsns_year, reprt_code, fs_div, sj_div,
        COALESCE(account_id, account_nm)
    );

CREATE INDEX idx_dart_fin_facts_stock
    ON dart_financial_facts (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX idx_dart_fin_facts_corp_year
    ON dart_financial_facts (corp_code, bsns_year);

CREATE INDEX idx_dart_fin_facts_account
    ON dart_financial_facts (account_id);
