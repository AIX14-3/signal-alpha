-- Signal Alpha DB constraint smoke tests.
--
-- Assumptions:
-- - migrations/001_extensions.sql through migrations/010_report_chunk_index.sql have been executed.
-- - seeds/001_seed_stocks.sql and seeds/002_seed_subscription_plans.sql have been executed.
-- - Run this file manually in a development database only.

BEGIN;

-- 1. stocks ticker duplicate prevention.
-- This should insert nothing because ticker 005930 is already seeded.
INSERT INTO stocks (
    ticker,
    name,
    market,
    sector
) VALUES (
    '005930',
    '삼성전자 중복 테스트',
    'KOSPI',
    '반도체'
)
ON CONFLICT (ticker) DO NOTHING;

SELECT
    'stocks ticker duplicate check' AS test_name,
    COUNT(*) AS ticker_count
FROM stocks
WHERE ticker = '005930';

-- 2. raw_documents source_hash duplicate prevention.
-- The second INSERT below should fail if uncommented because source_hash is UNIQUE.
WITH stock AS (
    SELECT id AS stock_id
    FROM stocks
    WHERE ticker = '005930'
),
run AS (
    INSERT INTO collector_runs (
        collector_type,
        run_mode,
        status
    ) VALUES (
        'REPORT',
        'manual',
        'success'
    )
    RETURNING id AS collector_run_id
)
INSERT INTO raw_documents (
    stock_id,
    collector_run_id,
    source_type,
    source_name,
    external_id,
    source_hash,
    title,
    source_url,
    published_at
)
SELECT
    stock.stock_id,
    run.collector_run_id,
    'REPORT',
    'TEST_SECURITIES',
    'TEST_CONSTRAINT_RAW_DUP',
    'TEST_SOURCE_HASH_DUPLICATE_001',
    'TEST duplicate source_hash report',
    'https://example.com/reports/test-constraint-duplicate',
    NOW()
FROM stock, run
ON CONFLICT (source_hash) DO NOTHING;

-- Expected failure if uncommented:
-- WITH stock AS (
--     SELECT id AS stock_id FROM stocks WHERE ticker = '005930'
-- )
-- INSERT INTO raw_documents (
--     stock_id,
--     source_type,
--     source_name,
--     external_id,
--     source_hash,
--     title,
--     published_at
-- )
-- SELECT
--     stock_id,
--     'REPORT',
--     'TEST_SECURITIES',
--     'TEST_CONSTRAINT_RAW_DUP_FAIL',
--     'TEST_SOURCE_HASH_DUPLICATE_001',
--     'TEST duplicate source_hash should fail',
--     NOW()
-- FROM stock;

-- 3. final_signals current unique index and trigger behavior.
-- The trigger should keep only one current signal for stock_id + signal_date + run_key.
WITH stock AS (
    SELECT id AS stock_id
    FROM stocks
    WHERE ticker = '005930'
),
request_row AS (
    INSERT INTO analysis_requests (
        stock_id,
        status,
        analysis_mode
    )
    SELECT
        stock_id,
        'completed',
        'full'
    FROM stock
    RETURNING id AS request_id, stock_id
),
result_v1 AS (
    INSERT INTO analysis_results (
        request_id,
        stock_id,
        analysis_date,
        run_key,
        source_signal_event_ids,
        base_score,
        analysis_mode,
        version
    )
    SELECT
        request_id,
        stock_id,
        CURRENT_DATE,
        'AM',
        ARRAY[]::BIGINT[],
        61.00,
        'full',
        'test_constraint_v1'
    FROM request_row
    ON CONFLICT (stock_id, analysis_date, analysis_mode, run_key, version)
    DO UPDATE SET base_score = EXCLUDED.base_score
    RETURNING id AS result_id, stock_id
),
signal_v1 AS (
    INSERT INTO final_signals (
        stock_id,
        analysis_result_id,
        signal_date,
        run_key,
        version,
        is_current,
        final_score,
        confidence,
        signal,
        source_agreement,
        warning_level,
        score_breakdown,
        summary,
        is_published,
        published_at
    )
    SELECT
        stock_id,
        result_id,
        CURRENT_DATE,
        'AM',
        'test_constraint_v1',
        TRUE,
        61.00,
        70.00,
        'positive',
        'MEDIUM',
        'NORMAL',
        '{"test": true, "version": 1}'::JSONB,
        'TEST final signal current v1',
        TRUE,
        NOW()
    FROM result_v1
    ON CONFLICT (stock_id, signal_date, run_key, version)
    DO UPDATE SET
        is_current = TRUE,
        final_score = EXCLUDED.final_score,
        confidence = EXCLUDED.confidence,
        score_breakdown = EXCLUDED.score_breakdown,
        summary = EXCLUDED.summary,
        is_published = EXCLUDED.is_published,
        published_at = EXCLUDED.published_at
    RETURNING id
),
result_v2 AS (
    INSERT INTO analysis_results (
        request_id,
        stock_id,
        analysis_date,
        run_key,
        source_signal_event_ids,
        base_score,
        analysis_mode,
        version
    )
    SELECT
        request_id,
        stock_id,
        CURRENT_DATE,
        'AM',
        ARRAY[]::BIGINT[],
        72.00,
        'full',
        'test_constraint_v2'
    FROM request_row
    ON CONFLICT (stock_id, analysis_date, analysis_mode, run_key, version)
    DO UPDATE SET base_score = EXCLUDED.base_score
    RETURNING id AS result_id, stock_id
)
INSERT INTO final_signals (
    stock_id,
    analysis_result_id,
    signal_date,
    run_key,
    version,
    is_current,
    final_score,
    confidence,
    signal,
    source_agreement,
    warning_level,
    score_breakdown,
    summary,
    is_published,
    published_at
)
SELECT
    stock_id,
    result_id,
    CURRENT_DATE,
    'AM',
    'test_constraint_v2',
    TRUE,
    72.00,
    74.00,
    'positive',
    'HIGH',
    'NORMAL',
    '{"test": true, "version": 2}'::JSONB,
    'TEST final signal current v2',
    TRUE,
    NOW()
FROM result_v2
ON CONFLICT (stock_id, signal_date, run_key, version)
DO UPDATE SET
    is_current = TRUE,
    final_score = EXCLUDED.final_score,
    confidence = EXCLUDED.confidence,
    score_breakdown = EXCLUDED.score_breakdown,
    summary = EXCLUDED.summary,
    is_published = EXCLUDED.is_published,
    published_at = EXCLUDED.published_at;

SELECT
    'final_signals current uniqueness check' AS test_name,
    stock_id,
    signal_date,
    run_key,
    COUNT(*) FILTER (WHERE is_current = TRUE) AS current_count,
    ARRAY_AGG(version ORDER BY version) AS versions
FROM final_signals
WHERE stock_id = (SELECT id FROM stocks WHERE ticker = '005930')
  AND signal_date = CURRENT_DATE
  AND run_key = 'AM'
  AND version IN ('test_constraint_v1', 'test_constraint_v2')
GROUP BY stock_id, signal_date, run_key;

-- 4. CHECK constraint examples.
-- Expected failure if uncommented: final_signals.signal only allows positive, negative, neutral, mixed.
-- INSERT INTO final_signals (
--     stock_id, analysis_result_id, signal_date, run_key, version, final_score,
--     confidence, signal, source_agreement, score_breakdown, summary
-- )
-- SELECT
--     s.id, ar.id, CURRENT_DATE, 'AM', 'test_bad_signal', 50,
--     50, 'buy', 'LOW', '{}'::JSONB, 'should fail'
-- FROM stocks s
-- JOIN analysis_results ar ON ar.stock_id = s.id
-- WHERE s.ticker = '005930'
-- LIMIT 1;

-- Expected failure if uncommented: analysis_results.base_score must be between 0 and 100.
-- INSERT INTO analysis_results (
--     stock_id, analysis_date, run_key, source_signal_event_ids, base_score, analysis_mode, version
-- )
-- SELECT id, CURRENT_DATE, 'AM', ARRAY[]::BIGINT[], 101, 'full', 'test_bad_base_score'
-- FROM stocks
-- WHERE ticker = '005930';

-- Expected failure if uncommented: signal_events.signal_direction only allows positive, negative, neutral, mixed, unknown.
-- INSERT INTO signal_events (
--     stock_id, source_document_id, event_hash, source_type, event_type,
--     event_date, signal_direction, impact_level, title
-- )
-- SELECT
--     sd.stock_id, sd.id, 'TEST_BAD_SIGNAL_DIRECTION_HASH', 'REPORT', 'target_price',
--     CURRENT_DATE, 'bullish', 'medium', 'should fail'
-- FROM source_documents sd
-- LIMIT 1;

-- 5. report_chunks unique constraint.
-- Same raw_document_id + chunk_index cannot be inserted twice.
-- Expected failure if uncommented after selecting an existing TEST raw_document_id:
-- INSERT INTO report_chunks (raw_document_id, stock_id, chunk_index, chunk_text)
-- SELECT raw_document_id, stock_id, chunk_index, 'duplicate chunk should fail'
-- FROM report_chunks
-- WHERE chunk_text LIKE 'TEST%'
-- LIMIT 1;

ROLLBACK;
