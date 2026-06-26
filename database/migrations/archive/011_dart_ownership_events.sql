-- 011_dart_ownership_events.sql
-- ============================================================================
-- L2 지분·내부자 적재 — OpenDART majorstock(대량보유 상황보고) + elestock(임원·주요주주 소유보고)
-- ----------------------------------------------------------------------------
-- 5%+ 대량보유 변동과 임원/주요주주 소유 변동을 단일 테이블에 통합 적재한다.
-- holder_type 으로 두 소스를 구분한다(major / executive / main_shareholder).
-- report_date(보고서 접수일 rcept_dt)가 사실상 known_at(PIT) 역할을 한다.
--
-- 멱등 자연키: (corp_code, rcept_no, holder_name, holder_type, line_seq). 같은 키로
-- 재수집되면 rcept_no 가 같거나 큰 경우에만 갱신한다(정정이 과거 재수집에 덮이지 않도록 — L1 006 동일).
-- line_seq: 한 보고서(rcept_no)에서 같은 보고자(holder_name)·유형이 여러 행으로 나뉘는 경우
-- (elestock 증권종류별, majorstock 변동내역 다건)를 보존하기 위한 행 일련번호. 수집기가
-- (rcept_no, holder_name, holder_type) 그룹 내 응답 순서로 0,1,2… 부여한다. 보고서는 불변이라
-- 재수집 시 순서가 안정적이어서 멱등이 유지된다.
--
-- ★4 내부자 순매수/매도 추세 신호·추세 집계는 분석 단계에서 추가한다.
-- ============================================================================

CREATE TABLE dart_ownership_events (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL,
    rcept_no VARCHAR(30) NOT NULL,              -- 접수번호(정정 추적 키)
    line_seq SMALLINT NOT NULL DEFAULT 0,       -- 동일 보고서·보고자 내 행 일련번호(증권종류/변동내역 구분)
    report_date DATE NOT NULL,                  -- 보고서 접수일(rcept_dt) = known_at
    holder_name VARCHAR(200) NOT NULL,          -- 보고자(repror, 원문 표기 — 정규화는 L5)
    holder_type VARCHAR(20) NOT NULL,           -- major(5%) / executive / main_shareholder
    shares NUMERIC(20,0),                       -- 보유 주식수
    ratio NUMERIC(8,4),                         -- 보유비율(%)
    shares_delta NUMERIC(20,0),                 -- 보유 주식수 증감
    ratio_delta NUMERIC(8,4),                   -- 보유비율 증감
    report_reason VARCHAR(100),                 -- 보고 사유/직위 등
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ownership_event UNIQUE (corp_code, rcept_no, holder_name, holder_type, line_seq)
);

CREATE INDEX idx_dart_ownership_stock
    ON dart_ownership_events (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX idx_dart_ownership_corp_date
    ON dart_ownership_events (corp_code, report_date);

CREATE INDEX idx_dart_ownership_holder_type
    ON dart_ownership_events (holder_type);
