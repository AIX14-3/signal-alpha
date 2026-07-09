-- 007_seed_jobkorea_company_ids.sql
-- target: collection
-- (hiring_portal_company_ids 는 COLLECTION 전용 — 수집 DB 만 시드)
--
-- Seeds hiring_portal_company_ids (이슈 #313): 종목 ↔ 잡코리아 회원번호.
-- 회원번호 = 잡코리아 내부 식별자(사업자번호 아님). 라이브 발굴로 확정한 것만 적재
-- (종목명 정확매칭 + 활성공고>0 검증). stock_id는 stocks.ticker로 해석(하드코딩 금지).
-- 재실행 안전: ON CONFLICT (stock_id, portal) DO NOTHING.
--
-- 매핑이 있으면 크롤러가 회원번호 직접수집(/Recruit/Co_Read/Recruit/C/<회원번호>)을 쓴다:
-- Selenium 불요·DOM 개편 내성·그 법인 공고만(협력사 노이즈 0). 없으면 키워드 검색 폴백.
--
-- 발굴 도구: scripts/discover_jobkorea_member_ids.py (읽기 전용).
--   검색결과 페이지의 임베디드 JSON("postingCompanyName"/"memberSystemNo")에서 쌍을 뽑아
--   정규화 완전일치 + 진행공고>0 을 실제 크롤러 파서(_parse_member_list)로 검증한 값이다.

INSERT INTO hiring_portal_company_ids (stock_id, portal, company_id)
SELECT s.id, v.portal, v.company_id
FROM (VALUES
    -- ── 1차 발굴 (2026-06-20, 이슈 #313) ──────────────────────────────────
    ('000660', 'JOBKOREA', '21493847'),   -- SK하이닉스(주)
    ('035420', 'JOBKOREA', '21572628'),   -- 네이버
    ('005380', 'JOBKOREA', '15421071'),   -- 현대자동차㈜
    ('352820', 'JOBKOREA', '29369775'),   -- ㈜하이브
    ('000100', 'JOBKOREA', '29957377'),   -- (주)유한양행

    -- ── 2차 발굴 (2026-07-09) — 전부 진행공고>0 실검증 ────────────────────
    -- 1차 때 "잡코리아 미노출"로 제외됐던 종목들이다. 당시 판정은 기업검색 링크
    -- (Co_Read/C)만 보고 내린 것인데, 그 링크에는 본 법인이 안 걸린다(협력사만 노출).
    -- 임베디드 JSON 경로로 재조사하니 전부 실재했다.
    ('000270', 'JOBKOREA', '153806'),     -- 기아㈜ (진행공고 2건)
    ('035720', 'JOBKOREA', '22185402'),   -- (주)카카오 (진행공고 29건)
    ('042700', 'JOBKOREA', '79937'),      -- 한미반도체(주) (진행공고 4건)
    ('068270', 'JOBKOREA', '11997301'),   -- ㈜셀트리온 (진행공고 2건)
    ('204320', 'JOBKOREA', '29738958'),   -- 만도(HL만도) (진행공고 1건)
    ('259960', 'JOBKOREA', '62332')       -- ㈜크래프톤 (진행공고 3건)
) AS v(ticker, portal, company_id)
JOIN stocks s ON s.ticker = v.ticker
ON CONFLICT (stock_id, portal) DO NOTHING;

-- 카카오 주의: 회원번호가 22185402 와 7746 두 개 존재하며 **같은 공고 29건**을 반환한다.
-- 검색 JSON 등장 빈도(38회 vs 2회)로 보아 22185402 가 현행 계정이라 그쪽을 택했다.
--
-- 미매핑 4종목 — 키워드 검색으로 폴백(회귀 없음). 2026-07-09 재확인:
--   005930 삼성전자          자사 채용사이트(samsungcareers) 위주, 잡코리아 정확매칭 공고 없음
--   041510 SM엔터테인먼트     동상
--   207940 삼성바이오로직스    후보 18건 전부 협력사·자회사
--   253450 스튜디오드래곤      후보 3건 전부 무관
--
-- ⚠️ discover 스크립트의 "미매핑"은 "그 검색으로 못 찾음"이지 "회원번호 부재"가 아니다.
--    SK하이닉스는 시드에 있는데도 그 도구로는 MISS 로 나온다(진행공고가 검색 첫 페이지에
--    안 잡히는 경우). 도구 출력만 믿고 기존 매핑을 제거하지 말 것.
