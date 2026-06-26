-- 017_hiring_portal_company_ids.sql
-- ============================================================================
-- hiring: 포털 기업 고유 ID 매핑 (이슈 #313, 스파이크 #266 승격)
-- ----------------------------------------------------------------------------
-- 배경
--   포털(잡코리아) 크롤러는 기업명 키워드 검색만 해서 협력사·대리점·계열사 공고를
--   대거 끌어온다(수집단계 선거부 #176로 사후 제거). 스파이크 #266에서 잡코리아가
--   기업 고유 ID(회원번호) 기반 공고 리스트를 서버렌더로 제공함을 확인했다:
--     GET /Recruit/Co_Read/Recruit/C/<회원번호> → 그 법인 진행공고만(노이즈 0).
--
-- 설계
--   종목 ↔ 포털 회원번호 매핑을 전용 테이블에 둔다. portal 컬럼으로 멀티포털 확장
--   대비(나중 'SARAMIN' 등). 식별자는 포털 내부 회원번호(잡코리아) 등 — 사업자번호(csn)
--   아님(잡코리아는 csn 미노출). 매핑이 있는 종목은 회원번호 직접 수집, 없으면 기존
--   키워드 검색으로 폴백(회귀 없음).
--   복합 유니크 (stock_id, portal) — 멀티포털 확장 시 안전 자산.
-- ============================================================================

CREATE TABLE hiring_portal_company_ids (
    id         BIGSERIAL PRIMARY KEY,
    stock_id   BIGINT NOT NULL REFERENCES stocks(id),
    portal     VARCHAR(20) NOT NULL,                  -- 'JOBKOREA' (확장: 'SARAMIN')
    company_id VARCHAR(40) NOT NULL,                  -- 포털 내부 회원번호 등
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hiring_portal_company_ids UNIQUE (stock_id, portal)
);
