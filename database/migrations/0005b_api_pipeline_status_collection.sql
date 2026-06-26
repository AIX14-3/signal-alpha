-- 0005b_api_pipeline_status_collection.sql
-- target: collection
-- ============================================================================
-- api.analysis_pipeline_status — 수집 DB 전용 (processing_queue 의존)
-- ----------------------------------------------------------------------------
-- 배경: 큐 단계 폴링(analytics)용 view. processing_queue 는 COLLECTION 테이블이라
--   백엔드 DB 에는 없으므로 이 view 는 수집 DB(target collection)에서만 만든다.
--   api 스키마 자체는 0005_api_read_contract(target all)에서 생성된다.
-- ============================================================================

CREATE OR REPLACE VIEW api.analysis_pipeline_status AS
SELECT
    processing_queue.id,
    processing_queue.stock_id,
    processing_queue.task_type,
    processing_queue.status,
    processing_queue.created_at,
    processing_queue.updated_at,
    stocks.ticker AS stock_code,
    stocks.name AS stock_name
FROM processing_queue
INNER JOIN stocks
    ON stocks.id = processing_queue.stock_id;
