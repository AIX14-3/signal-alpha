-- 20260701_1700_report_chunks_stock_id.sql
-- target: collection
-- ============================================================================
-- report_chunks 에 stock_id 비정규화 — 종목 필터를 exact 스캔으로 만들어 RAG 검색
-- 리콜을 정확히 보장한다(HNSW 전역 후보 후필터의 리콜 저하 근본 해결). 종목당 청크가
-- 적어 exact 정렬이 빠르다. stocks(id) FK 는 signal_episodes(#709)와 동일 패턴.
-- ============================================================================

-- 1) 컬럼 추가(우선 nullable — 기존 임베딩 행 백필 전이라 NOT NULL 불가)
ALTER TABLE report_chunks ADD COLUMN stock_id BIGINT REFERENCES stocks(id);

-- 2) 기존 청크 백필: report_raw_detail_id(=raw_documents.id) 로 stock_id 채움
UPDATE report_chunks rc
SET stock_id = rd.stock_id
FROM raw_documents rd
WHERE rd.id = rc.report_raw_detail_id
  AND rc.stock_id IS NULL;

-- 3) 백필 완료 → NOT NULL 확정
ALTER TABLE report_chunks ALTER COLUMN stock_id SET NOT NULL;

-- 4) 종목 필터용 btree 인덱스(플래너가 종목 청크만 골라 정확 정렬 → exact 리콜)
CREATE INDEX idx_report_chunks_stock ON report_chunks (stock_id);
