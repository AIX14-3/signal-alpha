-- 1. 컬럼 추가 (수집 여부 스위치 및 DataLab 약칭)
ALTER TABLE stocks 
    ADD COLUMN IF NOT EXISTS is_target BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS short_name VARCHAR(50);

-- 2. 부분 인덱스 생성 (is_target이 TRUE인 기업만 빠르게 필터링)
CREATE INDEX IF NOT EXISTS idx_stocks_is_target
    ON stocks (is_target)
    WHERE is_target = TRUE;