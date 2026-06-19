-- 016_hiring_search_trend.sql
-- ============================================================================
-- hiring: 부트스트랩 3년 검색 트렌드 원시 시계열 저장 (이슈 #291)
-- ----------------------------------------------------------------------------
-- 배경
--   bootstrap_hiring_baseline.py는 네이버 3년 주간 시계열(NaverDataLabRecord)을
--   받아 집계값(avg_search_volume + q1~q4_factor)만 hiring_baseline에 저장하고
--   원시 주간 레코드는 버린다. 그래서 대시보드의 "3년치 검색 기준선" 라벨이 있어도
--   실제 시계열이 DB에 없어 트렌드 차트를 못 그린다.
--
-- 목적 (관측성 — 분석 정확성과 무관)
--   부트스트랩이 받는 3년 주간 시계열을 저장해 (a) 트렌드 시각화, (b) baseline
--   산출 근거 감사/재현을 가능케 한다. analyzer는 집계값(avg+factors)만 쓰므로
--   본 테이블은 분석 결과에 영향을 주지 않는 순수 투명성/관측성 개선이다.
--
-- 설계
--   search_grouped는 종목당 키워드 한 그룹만 전송 → _parse_response가 단일 합산
--   시계열을 산출한다(종목당 (period_date) 단일 값). 따라서 자연키는
--   (stock_id, period_date)로 충분하며 keyword_group(예 'HYBE_HIRING_TREND')은
--   종목당 상수라 추적용으로만 저장한다.
--   부트스트랩 재실행 idempotent: 유니크 제약 위에서 ON CONFLICT DO UPDATE.
--
-- 규모
--   15종목 × ~156주 ≈ 2,340행(trivial). 유니크 제약이 생성하는 인덱스가
--   차트 쿼리(WHERE stock_id ORDER BY period_date)를 커버 → 별도 인덱스 불필요.
-- ============================================================================

CREATE TABLE hiring_search_trend (
    id            BIGSERIAL PRIMARY KEY,
    stock_id      BIGINT NOT NULL REFERENCES stocks(id),
    keyword_group VARCHAR(100) NOT NULL,                 -- 종목당 상수, 추적용
    period_date   DATE NOT NULL,
    search_index  NUMERIC(10,4) NOT NULL,                -- ratio (0~100 상대지수)
    period_type   VARCHAR(10) NOT NULL DEFAULT 'weekly',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hiring_search_trend_stock_date UNIQUE (stock_id, period_date)
);
