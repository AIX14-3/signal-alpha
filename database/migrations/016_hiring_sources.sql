-- 016_hiring_sources.sql
-- 기업별 공식 채용 사이트 크롤러 설정 테이블
-- ticker가 Single Source of Truth: 기업 추가/변경은 이 테이블 INSERT/UPDATE만으로 처리

DO $$ BEGIN
    CREATE TYPE hiring_crawler_type AS ENUM (
        'portal_saramin',     -- 사람인 키워드 검색 (별도 처리, 이 테이블 불필요)
        'portal_jobkorea',    -- 잡코리아 키워드 검색 (별도 처리, 이 테이블 불필요)
        'official_api',       -- 공식 API 기반 (driver=None, 삼성전자)
        'official_selenium',  -- 공식 SPA/ATS Selenium (NAVER, 카카오, SK하이닉스 등)
        'recruiter_kr',       -- recruiter.co.kr 집계 (HL만도, 셀트리온, 유한양행)
        'simple_site'         -- 정적/CMS 사이트 (한미반도체, 스튜디오드래곤, 삼성바이오)
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS hiring_sources (
    id            BIGSERIAL PRIMARY KEY,
    stock_id      BIGINT NOT NULL REFERENCES stocks(id),
    crawler_type  hiring_crawler_type NOT NULL,
    crawler_class VARCHAR(100),      -- 사용할 크롤러 클래스명 (예: SamsungCrawler)
    base_url      VARCHAR(500),      -- 기업 공식 채용 페이지 루트 URL
    extra_config  JSONB,             -- CSS selector, API 파라미터 등 추가 설정
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (stock_id, crawler_type)
);

CREATE INDEX IF NOT EXISTS idx_hiring_sources_active
    ON hiring_sources (stock_id)
    WHERE is_active = TRUE;

-- ── Seed: 15개 핵심 기업 공식 사이트 크롤러 설정 (ticker 기준) ──────────────────

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
