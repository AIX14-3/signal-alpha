-- 007_seed_jobkorea_company_ids.sql
-- target: collection
-- (hiring_portal_company_ids 는 COLLECTION 전용 — 수집 DB 만 시드)
--
-- Seeds hiring_portal_company_ids (이슈 #313): 종목 ↔ 잡코리아 회원번호.
-- 회원번호 = 잡코리아 내부 식별자(사업자번호 아님). 라이브 발굴로 확정한 것만 적재
-- (종목명 정확매칭 + 활성공고>0 검증). stock_id는 stocks.ticker로 해석(하드코딩 금지).
-- 재실행 안전: ON CONFLICT (stock_id, portal) DO NOTHING.
--
-- 미매핑 종목(삼성전자·카카오·기아·크래프톤·한미반도체·HL만도·SM·스튜디오드래곤·
-- 삼성바이오·셀트리온 등 — 잡코리아 정확매칭 공고 페이지 미노출/자사채용 위주)은
-- 의도적으로 제외 → 그 종목은 기존 키워드 검색으로 폴백(회귀 없음). 매핑은 확장 가능.

INSERT INTO hiring_portal_company_ids (stock_id, portal, company_id)
SELECT s.id, v.portal, v.company_id
FROM (VALUES
    ('000660', 'JOBKOREA', '21493847'),   -- SK하이닉스(주)
    ('035420', 'JOBKOREA', '21572628'),   -- 네이버
    ('005380', 'JOBKOREA', '15421071'),   -- 현대자동차㈜
    ('352820', 'JOBKOREA', '29369775'),   -- ㈜하이브
    ('000100', 'JOBKOREA', '29957377')    -- (주)유한양행
) AS v(ticker, portal, company_id)
JOIN stocks s ON s.ticker = v.ticker
ON CONFLICT (stock_id, portal) DO NOTHING;
