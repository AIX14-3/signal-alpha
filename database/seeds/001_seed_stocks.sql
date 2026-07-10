-- 001_seed_stocks.sql
-- target: all
-- (stocks 는 PUBLISHED — 수집·백엔드 양쪽 DB 에 시드)
-- 27개 핵심 기업 기본 정보 + 수집 대상(is_target)/약칭(short_name) 설정.
-- 재실행 안전(idempotent): ON CONFLICT 기반.
-- universe 확장(15→27): jasoseol 커버리지 ≥15 기준 Tier-1 12종목 추가(2026-06-25, A 결정).

-- 0. id 시퀀스 재동기화.
-- 백엔드 DB 의 stocks 는 발행 러너가 수집 DB 의 id 를 '명시 id' 로 복사한다(양쪽 stock_id 일치).
-- 명시 id INSERT 는 시퀀스를 전진시키지 않으므로 시퀀스가 max(id) 뒤에 남는다. 그 상태에서
-- 아래 INSERT 가 미등재 티커를 새로 넣으면 nextval 이 이미 점유된 id 를 뽑아 stocks_pkey 를
-- 위반한다(#863 배포 시 db-migrate-backend Job 이 이걸로 반복 실패 → Argo Sync 훅 무한 대기).
-- ON CONFLICT (ticker) 는 티커 중복만 잡고 pkey 충돌은 못 잡으므로, 시퀀스를 먼저 맞춘다.
DO $$
DECLARE
    seq text := pg_get_serial_sequence('stocks', 'id');
BEGIN
    IF seq IS NOT NULL THEN
        PERFORM setval(seq, COALESCE((SELECT max(id) FROM stocks), 0) + 1, false);
    END IF;
END $$;

-- 1. 27개 핵심 기업 기본 정보 주입 (이미 테이블에 있으면 sector만 최신화)
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
    ('000100', '유한양행',         'KOSPI',  '제약'),
    -- universe 확장 Tier-1 12종목 (jasoseol 커버리지 ≥15, 2026-06-25)
    ('323410', '카카오뱅크',       'KOSPI',  '인터넷은행'),
    ('012330', '현대모비스',       'KOSPI',  '자동차부품'),
    ('066570', 'LG전자',           'KOSPI',  '가전'),
    ('096770', 'SK이노베이션',     'KOSPI',  '정유화학'),
    ('051910', 'LG화학',           'KOSPI',  '화학'),
    ('373220', 'LG에너지솔루션',   'KOSPI',  '2차전지'),
    ('377300', '카카오페이',       'KOSPI',  '핀테크'),
    ('015760', '한국전력',         'KOSPI',  '유틸리티'),
    ('028260', '삼성물산',         'KOSPI',  '종합상사'),
    ('086280', '현대글로비스',     'KOSPI',  '물류'),
    ('003670', '포스코퓨처엠',     'KOSPI',  '2차전지소재'),
    ('032830', '삼성생명',         'KOSPI',  '보험')
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
        -- 한국전력: jasoseol 공고명이 '한국전력공사'(종목명보다 김) → short_name 으로 매칭(#496)
        WHEN '015760' THEN '한국전력공사'
        ELSE NULL -- 약칭이 필요 없는 나머지 기업은 NULL 유지
    END
WHERE ticker IN (
    '005930','000660','042700','035420','035720',
    '259960','005380','000270','204320','352820',
    '041510','253450','207940','068270','000100',
    -- universe 확장 Tier-1 12종목 (short_name 은 대부분 NULL; 한국전력만 위 CASE 에서 '한국전력공사' #496)
    '323410','012330','066570','096770','051910',
    '373220','377300','015760','028260','086280',
    '003670','032830'
);
