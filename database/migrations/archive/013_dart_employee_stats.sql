-- 013_dart_employee_stats.sql
-- ============================================================================
-- L3 임직원 현황 적재 — OpenDART empSttus(직원 현황, 정기보고서)
-- ----------------------------------------------------------------------------
-- 사업부문·정규/계약 인원, 평균근속, 1인평균급여 추세 = 고용 모멘텀(★F headcount_yoy).
-- 종목별로 연도×보고서코드 단위로 empSttus 를 호출해 적재한다.
--
-- 라이브 검증(삼성 00126380, 2024 사업보고서)으로 확정된 행 구조:
--   empSttus 는 사업부문(fo_bbm) × 성별(sexdstn) 로 행이 쪼개진다(DX×남/여, DS×남/여, 성별합계×남/여…).
--   1인평균급여(jan_salary_am)·연간급여총액(fyer_salary_totamt)은 합계 행에만 채워지고
--   사업부문별 행은 '-'(빈값) 이다 → 수집기에서 None 정규화.
--
-- 멱등 자연키: (corp_code, bsns_year, reprt_code, COALESCE(segment,''), COALESCE(sex,''), line_seq).
--   segment(fo_bbm)/sex(sexdstn) 둘 다 차원이므로 자연키에 포함해야 충돌하지 않는다.
--   line_seq: 같은 (segment, sex) 가 한 보고서에서 여러 행으로 나오는 비표준 공시(사업부문명 중복·
--   조직 재편 행 등)를 보존하기 위한 행 일련번호. 수집기가 (rcept_no, segment, sex) 그룹 내 응답
--   순서로 0,1,2… 부여한다. 보고서는 불변이라 재수집 시 순서가 안정적이어서 멱등이 유지된다(L2 011 동일).
--   COALESCE 표현식 유니크 인덱스로 NULL 차원도 중복을 막는다(L1 006 패턴).
-- 정정 단조성: 같은 키로 재수집되면 rcept_no 가 같거나 큰 경우에만 갱신한다(006/011 동일).
--
-- ★F headcount_yoy 신호·NPS/채용 교차는 분석 단계에서 추가한다.
-- ============================================================================

CREATE TABLE dart_employee_stats (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL,
    rcept_no VARCHAR(30) NOT NULL,              -- 접수번호(정정 추적 키)
    line_seq SMALLINT NOT NULL DEFAULT 0,       -- 동일 (segment, sex) 다중행 보존용 행 일련번호
    bsns_year SMALLINT NOT NULL,
    reprt_code VARCHAR(5) NOT NULL,             -- 11011=사업/11012=반기/11013=1Q/11014=3Q
    segment VARCHAR(100),                       -- 사업부문(fo_bbm). 합계 행 포함
    sex VARCHAR(10),                            -- 성별(sexdstn): 남/여
    headcount INTEGER,                          -- 합계 인원(sm)
    regular_count INTEGER,                      -- 정규직(rgllbr_co)
    contract_count INTEGER,                     -- 계약직(cnttk_co)
    avg_tenure_years NUMERIC(6,2),             -- 평균 근속연수(avrg_cnwk_sdytrn)
    avg_salary_krw NUMERIC(20,0),              -- 1인평균 급여액(jan_salary_am, 전사합계 행만)
    salary_total_krw NUMERIC(20,0),            -- 연간 급여 총액(fyer_salary_totamt, 전사합계 행만)
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 멱등 자연키(segment/sex 빈 값은 '' 폴백). ON CONFLICT 추론 대상.
CREATE UNIQUE INDEX uq_employee_stats
    ON dart_employee_stats (
        corp_code, bsns_year, reprt_code,
        COALESCE(segment, ''), COALESCE(sex, ''), line_seq
    );

CREATE INDEX idx_dart_employee_stock
    ON dart_employee_stats (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX idx_dart_employee_corp_year
    ON dart_employee_stats (corp_code, bsns_year);
