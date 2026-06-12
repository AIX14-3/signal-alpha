-- hiring_raw_details에 observed_date 컬럼 추가
-- HiringAnalyzer가 날짜 기반 필터링(14일 이동평균, 오늘 공고 수 조회)에 사용

ALTER TABLE hiring_raw_details ADD COLUMN observed_date DATE;

UPDATE hiring_raw_details hrd
SET observed_date = rd.published_at::DATE
FROM raw_documents rd
WHERE rd.id = hrd.raw_document_id;

ALTER TABLE hiring_raw_details ALTER COLUMN observed_date SET NOT NULL;

CREATE INDEX idx_hiring_observed ON hiring_raw_details (stock_id, observed_date DESC);
