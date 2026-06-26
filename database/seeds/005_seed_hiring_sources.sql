-- 005_seed_hiring_sources.sql
-- target: collection
-- (hiring_sources 는 COLLECTION 전용 — 수집 DB 만 시드)
-- 15개 핵심 기업 공식 채용 사이트 크롤러 설정 (ticker 기준).
-- 구 016_hiring_sources.sql 마이그레이션에 인라인이던 시드를 컨벤션대로 분리.
-- ticker가 Single Source of Truth: 기업 추가/변경은 이 시드 INSERT/UPDATE로 처리.
-- ON CONFLICT 기반 재실행 안전(idempotent).

-- 삼성전자 (005930): 공식 API, driver 불필요
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_api', 'SamsungCrawler', 'https://www.samsungcareers.com/'
FROM stocks s WHERE s.ticker = '005930'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- SK하이닉스 (000660): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'SKHynixCrawler', 'https://talent.skhynix.com'
FROM stocks s WHERE s.ticker = '000660'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- NAVER (035420): requests + Selenium fallback (driver 전달)
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'NaverCrawler', 'https://recruit.navercorp.com'
FROM stocks s WHERE s.ticker = '035420'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 카카오 (035720): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'KakaoCrawler', 'https://careers.kakao.com'
FROM stocks s WHERE s.ticker = '035720'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 크래프톤 (259960): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'KraftonCrawler', 'https://www.krafton.com'
FROM stocks s WHERE s.ticker = '259960'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 현대자동차 (005380): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'HyundaiCrawler', NULL
FROM stocks s WHERE s.ticker = '005380'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 기아 (000270): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'KiaCrawler', NULL
FROM stocks s WHERE s.ticker = '000270'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- HYBE (352820): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'HybeCrawler', NULL
FROM stocks s WHERE s.ticker = '352820'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- SM엔터테인먼트 (041510): Selenium SPA
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'official_selenium', 'SMCrawler', NULL
FROM stocks s WHERE s.ticker = '041510'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- HL만도 (204320): recruiter.co.kr
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'recruiter_kr', 'RecruiterKrCrawler', NULL
FROM stocks s WHERE s.ticker = '204320'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 셀트리온 (068270): recruiter.co.kr
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'recruiter_kr', 'RecruiterKrCrawler', NULL
FROM stocks s WHERE s.ticker = '068270'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 유한양행 (000100): recruiter.co.kr
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'recruiter_kr', 'RecruiterKrCrawler', NULL
FROM stocks s WHERE s.ticker = '000100'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 한미반도체 (042700): 정적 사이트
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'simple_site', 'SimpleSiteCrawler',
       'https://www.hanmisemi.com/?module=Html&action=SiteComp&sSubNo=17'
FROM stocks s WHERE s.ticker = '042700'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 스튜디오드래곤 (253450): 정적 사이트
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'simple_site', 'SimpleSiteCrawler',
       'https://www.studiodragon.net/ko/etc/talent/'
FROM stocks s WHERE s.ticker = '253450'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;

-- 삼성바이오로직스 (207940): 정적 사이트
INSERT INTO hiring_sources (stock_id, crawler_type, crawler_class, base_url)
SELECT s.id, 'simple_site', 'SimpleSiteCrawler',
       'https://samsungbiologics.com/kr/careers/apply/how-to-apply'
FROM stocks s WHERE s.ticker = '207940'
ON CONFLICT (stock_id, crawler_type) DO NOTHING;
