-- 수집 대상 15개 기업 초기 데이터 주입
-- Python 코드의 COMPANY_STOCK_MAP을 DB로 이전 (Single Source of Truth)
-- ON CONFLICT DO UPDATE: 이미 존재하는 경우 sector만 갱신하고 나머지는 유지
INSERT INTO stocks (ticker, name, market, sector) VALUES
    ('005930', '삼성전자',         'KOSPI',  '반도체'),
    ('000660', 'SK하이닉스',       'KOSPI',  '반도체'),
    ('042700', '한미반도체',       'KOSPI',  '반도체장비'),
    ('035420', 'NAVER',            'KOSPI',  '인터넷'),
    ('035720', '카카오',           'KOSPI',  '인터넷'),
    ('259960', '크래프톤',         'KOSPI',  '게임'),
    ('005380', '현대자동차',       'KOSPI',  '자동차'),
    ('000270', '기아',             'KOSPI',  '자동차'),
    ('204320', 'HL만도',           'KOSPI',  '자동차부품'),
    ('352820', 'HYBE',             'KOSPI',  '엔터'),
    ('041510', 'SM엔터테인먼트',   'KOSPI',  '엔터'),
    ('253450', '스튜디오드래곤',   'KOSDAQ', '콘텐츠'),
    ('207940', '삼성바이오로직스', 'KOSPI',  '바이오'),
    ('068270', '셀트리온',         'KOSPI',  '바이오'),
    ('000100', '유한양행',         'KOSPI',  '제약')
ON CONFLICT (ticker) DO UPDATE
    SET sector = EXCLUDED.sector;

-- is_target 활성화 (015 마이그레이션보다 먼저 실행되는 경우 대비)
UPDATE stocks
SET is_target = TRUE
WHERE ticker IN (
    '005930','000660','042700',
    '035420','035720','259960',
    '005380','000270','204320',
    '352820','041510','253450',
    '207940','068270','000100'
);

-- 적용 확인
-- SELECT ticker, name, market, sector, is_target FROM stocks WHERE is_target = TRUE ORDER BY name;
