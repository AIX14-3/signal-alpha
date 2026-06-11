-- 1. 15개 핵심 기업 기본 정보 주입 (이미 테이블에 있으면 sector만 최신화)
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
    -- is_target / short_name 은 갱신하지 않음: 이미 활성화된 기업 설정 보존

-- 2. 대상 기업 활성화 및 약칭(short_name) 매핑 업데이트
-- (이름 대신 '티커' 기준으로 일괄 처리하여 정밀도를 높입니다)
UPDATE stocks
SET is_target = TRUE,
    short_name = CASE ticker
        WHEN '005930' THEN '삼성'
        WHEN '000660' THEN '하이닉스'
        WHEN '207940' THEN '삼성바이오'
        WHEN '041510' THEN 'SM'
        WHEN '005380' THEN '현대'
        WHEN '035420' THEN '네이버'
        WHEN '352820' THEN '하이브'
        WHEN '204320' THEN '만도'
        ELSE NULL -- 약칭이 필요 없는 나머지 7개 기업은 NULL 유지
    END
WHERE ticker IN (
    '005930','000660','042700','035420','035720',
    '259960','005380','000270','204320','352820',
    '041510','253450','207940','068270','000100'
);