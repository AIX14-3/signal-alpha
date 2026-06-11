-- 019_processing_queue_nullable_stock.sql
--
-- DataLab tasks are enqueued by category, not by stock. The Normalizer resolves
-- category → stock(s) via datalab_category_stocks, so the Collector must enqueue
-- NORMALIZE_DATALAB rows with stock_id = NULL.
--
-- The original definition (005) declared stock_id NOT NULL, which made the
-- DataLab collector's INSERT fail at runtime. Relax it to nullable; the FK to
-- stocks(id) still applies when a value is present.

ALTER TABLE processing_queue
    ALTER COLUMN stock_id DROP NOT NULL;
