-- stocks 테이블에 수집 대상 스위치 컬럼 추가
-- is_target = TRUE  → 모든 수집기(Hiring/DataLab/Patent 등)의 활성 대상
-- is_target = FALSE → 수집 제외 (기본값)
-- 기업 추가/제거는 SQL UPDATE 한 줄로 처리 — 코드 수정 불필요

ALTER TABLE stocks
    ADD COLUMN IF NOT EXISTS is_target BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_stocks_is_target
    ON stocks (is_target)
    WHERE is_target = TRUE;

-- 현재 수집 대상 15개 기업 활성화
-- (stocks 테이블에 이미 등록된 기업이어야 합니다)
UPDATE stocks
SET is_target = TRUE
WHERE name IN (
    '삼성전자',
    'SK하이닉스',
    '한미반도체',
    'NAVER',
    '카카오',
    '크래프톤',
    '현대자동차',
    '기아',
    'HL만도',
    'HYBE',
    'SM엔터테인먼트',
    '스튜디오드래곤',
    '삼성바이오로직스',
    '셀트리온',
    '유한양행'
);

-- 적용 확인용 (실행 후 눈으로 검증)
-- SELECT name, sector, is_target FROM stocks ORDER BY is_target DESC, name;
