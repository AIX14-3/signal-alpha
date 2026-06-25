-- Signal Alpha MVP sample data flow test.
--
-- Assumptions:
-- - migrations/001_extensions.sql through migrations/010_report_chunk_index.sql have been executed.
-- - seeds/001_seed_stocks.sql and seeds/002_seed_subscription_plans.sql have been executed.
-- - This script uses TEST-prefixed values and is intended for development databases only.

BEGIN;

WITH stock AS (
    SELECT id AS stock_id
    FROM stocks
    WHERE ticker = '005930'
),
test_user AS (
    INSERT INTO users (
        member_code,
        email,
        password_hash,
        nickname,
        agreed_risk,
        is_verified
    ) VALUES (
        'TEST_USER_001',
        'test.user@example.com',
        'TEST_PASSWORD_HASH_NOT_FOR_PRODUCTION',
        'TEST USER',
        TRUE,
        TRUE
    )
    ON CONFLICT (member_code) DO UPDATE SET
        email = EXCLUDED.email,
        nickname = EXCLUDED.nickname,
        agreed_risk = EXCLUDED.agreed_risk,
        is_verified = EXCLUDED.is_verified
    RETURNING id AS user_id
),
collector_run AS (
    INSERT INTO collector_runs (
        collector_type,
        run_mode,
        status,
        collected_count,
        inserted_count
    ) VALUES (
        'REPORT',
        'manual',
        'success',
        1,
        1
    )
    RETURNING id AS collector_run_id
),
raw_doc AS (
    INSERT INTO raw_documents (
        stock_id,
        collector_run_id,
        source_type,
        source_name,
        external_id,
        source_hash,
        title,
        source_url,
        published_at,
        collect_status
    )
    SELECT
        stock.stock_id,
        collector_run.collector_run_id,
        'REPORT',
        'TEST_SECURITIES',
        'TEST_REPORT_20260608_005930',
        'TEST_REPORT_HASH_20260608_005930',
        'TEST 삼성전자 AI 반도체 수요 확대 리포트',
        'https://example.com/reports/test-samsung-ai-semiconductor',
        NOW(),
        'success'
    FROM stock, collector_run
    ON CONFLICT (source_hash) DO UPDATE SET
        title = EXCLUDED.title,
        source_url = EXCLUDED.source_url,
        collect_status = EXCLUDED.collect_status
    RETURNING id AS raw_document_id, stock_id
),
report_detail AS (
    INSERT INTO report_raw_details (
        raw_document_id,
        stock_id,
        securities_firm,
        analyst_name,
        publish_date,
        investment_opinion,
        target_price,
        previous_target_price,
        current_price_at_publish,
        upside_pct,
        has_pdf,
        pdf_url,
        extracted_text,
        parsing_status,
        extra_payload
    )
    SELECT
        raw_document_id,
        stock_id,
        'TEST_SECURITIES',
        'TEST_ANALYST',
        CURRENT_DATE,
        'BUY',
        95000,
        88000,
        78000,
        21.79,
        TRUE,
        'https://example.com/reports/test-samsung-ai-semiconductor.pdf',
        'TEST extracted report text. AI semiconductor demand and memory price recovery support earnings growth.',
        'success',
        '{"test": true, "source": "sample_flow"}'::JSONB
    FROM raw_doc
    ON CONFLICT (raw_document_id) DO UPDATE SET
        target_price = EXCLUDED.target_price,
        previous_target_price = EXCLUDED.previous_target_price,
        current_price_at_publish = EXCLUDED.current_price_at_publish,
        upside_pct = EXCLUDED.upside_pct,
        extracted_text = EXCLUDED.extracted_text,
        parsing_status = EXCLUDED.parsing_status,
        extra_payload = EXCLUDED.extra_payload
    RETURNING raw_document_id, stock_id
),
queue_row AS (
    INSERT INTO processing_queue (
        stock_id,
        task_type,
        status,
        priority,
        source_raw_ids,
        task_context
    )
    SELECT
        raw_doc.stock_id,
        'NORMALIZE_REPORT',
        'pending',
        'batch',
        ARRAY[raw_doc.raw_document_id],
        '{"test": true, "flow": "sample_report_rag"}'::JSONB
    FROM raw_doc
    RETURNING id AS queue_id
),
source_doc AS (
    INSERT INTO source_documents (
        raw_document_id,
        stock_id,
        source_type,
        source_name,
        title,
        source_url,
        published_at,
        collected_at,
        reliability_level,
        is_official
    )
    SELECT
        raw_documents.id,
        raw_documents.stock_id,
        raw_documents.source_type,
        raw_documents.source_name,
        raw_documents.title,
        raw_documents.source_url,
        raw_documents.published_at,
        raw_documents.collected_at,
        'medium',
        FALSE
    FROM raw_documents
    WHERE raw_documents.source_hash = 'TEST_REPORT_HASH_20260608_005930'
    ON CONFLICT (raw_document_id) DO UPDATE SET
        title = EXCLUDED.title,
        source_url = EXCLUDED.source_url,
        reliability_level = EXCLUDED.reliability_level
    RETURNING id AS source_document_id, stock_id
),
event_row AS (
    INSERT INTO signal_events (
        stock_id,
        source_document_id,
        event_hash,
        source_type,
        event_type,
        event_date,
        signal_direction,
        impact_level,
        title,
        summary,
        evidence_text,
        evidence_url
    )
    SELECT
        stock_id,
        source_document_id,
        'TEST_SIGNAL_EVENT_HASH_REPORT_005930_AM',
        'REPORT',
        'target_price_raise',
        CURRENT_DATE,
        'positive',
        'medium',
        'TEST 리포트 목표가 상향',
        'TEST Report indicates positive earnings momentum from AI semiconductor demand.',
        'Target price 95,000 KRW and upside 21.79%.',
        'https://example.com/reports/test-samsung-ai-semiconductor'
    FROM source_doc
    ON CONFLICT (event_hash) DO UPDATE SET
        summary = EXCLUDED.summary,
        evidence_text = EXCLUDED.evidence_text
    RETURNING id AS signal_event_id, stock_id
),
metric_target AS (
    INSERT INTO signal_metrics (
        signal_event_id,
        metric_name,
        metric_value,
        metric_unit,
        previous_value,
        change_pct
    )
    SELECT
        signal_event_id,
        'target_price',
        95000,
        'KRW',
        88000,
        7.95
    FROM event_row
    ON CONFLICT (signal_event_id, metric_name) DO UPDATE SET
        metric_value = EXCLUDED.metric_value,
        previous_value = EXCLUDED.previous_value,
        change_pct = EXCLUDED.change_pct
    RETURNING signal_event_id
),
metric_upside AS (
    INSERT INTO signal_metrics (
        signal_event_id,
        metric_name,
        metric_value,
        metric_unit
    )
    SELECT
        signal_event_id,
        'upside_pct',
        21.79,
        'percent'
    FROM event_row
    ON CONFLICT (signal_event_id, metric_name) DO UPDATE SET
        metric_value = EXCLUDED.metric_value,
        metric_unit = EXCLUDED.metric_unit
    RETURNING signal_event_id
),
analysis_request AS (
    INSERT INTO analysis_requests (
        user_id,
        stock_id,
        status,
        analysis_mode,
        completed_at
    )
    SELECT
        test_user.user_id,
        stock.stock_id,
        'completed',
        'full',
        NOW()
    FROM test_user, stock
    RETURNING id AS request_id, stock_id
),
analysis_result AS (
    INSERT INTO analysis_results (
        request_id,
        stock_id,
        analysis_date,
        run_key,
        source_signal_event_ids,
        base_score,
        pre_xgb_score,
        analysis_mode,
        version
    )
    SELECT
        analysis_request.request_id,
        analysis_request.stock_id,
        CURRENT_DATE,
        'AM',
        ARRAY[event_row.signal_event_id],
        78.50,
        78.50,
        'full',
        'test_sample_flow_v1'
    FROM analysis_request, event_row
    ON CONFLICT (stock_id, analysis_date, analysis_mode, run_key, version)
    DO UPDATE SET
        source_signal_event_ids = EXCLUDED.source_signal_event_ids,
        base_score = EXCLUDED.base_score,
        pre_xgb_score = EXCLUDED.pre_xgb_score
    RETURNING id AS analysis_result_id, stock_id, source_signal_event_ids
),
agent_result AS (
    INSERT INTO agent_results (
        result_id,
        stock_id,
        debate_method,
        source_signal_event_ids,
        method_score,
        method_signal,
        method_detail,
        reliability_score,
        evidence_quality,
        llm_model,
        prompt_ver
    )
    SELECT
        analysis_result_id,
        stock_id,
        'D-1',
        source_signal_event_ids,
        80.00,
        'positive',
        '{"test": true, "rationale": "Report chunks support positive target price signal."}'::JSONB,
        72.00,
        75.00,
        'TEST_LLM',
        'test-1.0'
    FROM analysis_result
    ON CONFLICT (result_id, debate_method) DO UPDATE SET
        source_signal_event_ids = EXCLUDED.source_signal_event_ids,
        method_score = EXCLUDED.method_score,
        method_signal = EXCLUDED.method_signal,
        method_detail = EXCLUDED.method_detail
    RETURNING id AS agent_result_id, result_id, stock_id
),
final_signal AS (
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
        bull_point,
        bear_point,
        needs_review,
        min_plan_required,
        is_published,
        published_at
    )
    SELECT
        stock_id,
        analysis_result_id,
        CURRENT_DATE,
        'AM',
        'test_sample_flow_v1',
        TRUE,
        80.00,
        76.00,
        'positive',
        'HIGH',
        'NORMAL',
        '{"report": 80.0, "metrics": {"target_price": 95000, "upside_pct": 21.79}}'::JSONB,
        'TEST final signal: report RAG and metrics indicate positive momentum.',
        'AI semiconductor demand and target price raise support upside.',
        'Single sample report only; test data.',
        FALSE,
        'free',
        TRUE,
        NOW()
    FROM analysis_result
    ON CONFLICT (stock_id, signal_date, run_key, version)
    DO UPDATE SET
        is_current = TRUE,
        final_score = EXCLUDED.final_score,
        confidence = EXCLUDED.confidence,
        signal = EXCLUDED.signal,
        source_agreement = EXCLUDED.source_agreement,
        score_breakdown = EXCLUDED.score_breakdown,
        summary = EXCLUDED.summary,
        bull_point = EXCLUDED.bull_point,
        bear_point = EXCLUDED.bear_point,
        is_published = EXCLUDED.is_published,
        published_at = EXCLUDED.published_at
    RETURNING id AS final_signal_id, stock_id
),
read_row AS (
    INSERT INTO user_signal_reads (
        user_id,
        final_signal_id
    )
    SELECT
        test_user.user_id,
        final_signal.final_signal_id
    FROM test_user, final_signal
    ON CONFLICT (user_id, final_signal_id) DO NOTHING
    RETURNING id AS read_id
),
watchlist_row AS (
    INSERT INTO watchlists (
        user_id,
        stock_id,
        notification_enabled
    )
    SELECT
        test_user.user_id,
        stock.stock_id,
        TRUE
    FROM test_user, stock
    ON CONFLICT (user_id, stock_id) DO NOTHING
    RETURNING id AS watchlist_id
),
journal_row AS (
    INSERT INTO signal_journals (
        user_id,
        final_signal_id,
        stock_id,
        user_view,
        user_memo,
        decision_type,
        decision_reason,
        signal_score_at_time,
        signal_value_at_time,
        price_at_time,
        source_agreement_at_time
    )
    SELECT
        test_user.user_id,
        final_signal.final_signal_id,
        final_signal.stock_id,
        'bullish',
        'TEST journal memo for sample flow.',
        'watch',
        'Report RAG sample flow generated positive signal.',
        80.00,
        'positive',
        78000,
        'HIGH'
    FROM test_user, final_signal
    RETURNING id AS journal_id
)
SELECT
    final_signals.id AS final_signal_id,
    stocks.ticker,
    stocks.name AS stock_name,
    final_signals.signal_date,
    final_signals.run_key,
    final_signals.is_current,
    final_signals.final_score,
    final_signals.signal,
    analysis_results.id AS analysis_result_id,
    agent_results.id AS agent_result_id,
    agent_results.debate_method,
    signal_events.id AS signal_event_id,
    signal_events.event_type,
    signal_events.signal_direction,
    EXISTS (
        SELECT 1
        FROM user_signal_reads
        WHERE user_signal_reads.final_signal_id = final_signals.id
          AND user_signal_reads.user_id = (SELECT user_id FROM test_user)
    ) AS user_read_exists,
    EXISTS (
        SELECT 1
        FROM watchlists
        WHERE watchlists.stock_id = stocks.id
          AND watchlists.user_id = (SELECT user_id FROM test_user)
    ) AS watchlist_exists,
    EXISTS (
        SELECT 1
        FROM signal_journals
        WHERE signal_journals.final_signal_id = final_signals.id
          AND signal_journals.user_id = (SELECT user_id FROM test_user)
    ) AS journal_exists
FROM final_signals
JOIN stocks
  ON stocks.id = final_signals.stock_id
JOIN analysis_results
  ON analysis_results.id = final_signals.analysis_result_id
JOIN agent_results
  ON agent_results.result_id = analysis_results.id
LEFT JOIN LATERAL UNNEST(analysis_results.source_signal_event_ids) AS event_ids(signal_event_id)
  ON TRUE
LEFT JOIN signal_events
  ON signal_events.id = event_ids.signal_event_id
WHERE stocks.ticker = '005930'
  AND final_signals.version = 'test_sample_flow_v1'
  AND final_signals.run_key = 'AM'
ORDER BY final_signals.created_at DESC
LIMIT 5;

-- The sample flow is wrapped in a transaction and rolled back so it can be run repeatedly.
-- Change ROLLBACK to COMMIT only if you intentionally want to keep the TEST data.
ROLLBACK;
