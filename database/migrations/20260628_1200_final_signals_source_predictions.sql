-- 20260628_1200_final_signals_source_predictions.sql
-- target: all
-- ============================================================================
-- final_signals.source_predictions(JSONB) 추가 + api view 재전개 (C안 P3)
-- ----------------------------------------------------------------------------
-- 배경
--   RETURN_COMBINE 이 소스별 6개(주가/datalab/특허/채용/DART/리포트) + 통합 1개 = 7개
--   예측률을 meta_signals(per-source run_key)에 적재한다. 소비자(web)는 백엔드 DB 의
--   api.signals_current 만 읽으므로, 7개 예측률을 발행 경로에 태우려면 final_signals 에
--   JSONB 컬럼으로 실어야 한다(publisher 가 SELECT * 로 백엔드에 복사 → api view 노출).
-- 설계 (ml_* return 채널 20260626_1600 패턴 동일)
--   - source_predictions: {run_key: {final_score, direction, confidence, model_count}} JSONB. 결측 NULL.
--   - api view 는 final_signals.* 라 * 가 옛 컬럼셋에 고정 → CREATE OR REPLACE 는 컬럼 중간
--     삽입을 못 하므로 DROP + CREATE 로 재전개(정의는 0005 원본과 동일). 멱등: IF EXISTS/IF NOT EXISTS.
--   - signal_backend 롤 존재 시 SELECT 재부여. target: all — 수집/백엔드 양쪽 api 스키마에 적용.
-- ============================================================================

ALTER TABLE final_signals
    ADD COLUMN IF NOT EXISTS source_predictions JSONB;  -- 소스별 6 + 통합 1 = 7개 예측률(C안 P3)

DROP VIEW IF EXISTS api.signals_current;
CREATE VIEW api.signals_current AS
SELECT
    final_signals.*,
    stocks.ticker,
    stocks.name,
    stocks.market,
    stocks.sector,
    analysis_results.analysis_mode,
    analysis_results.base_score,
    analysis_results.warning AS analysis_warning
FROM final_signals
INNER JOIN stocks
    ON stocks.id = final_signals.stock_id
INNER JOIN analysis_results
    ON analysis_results.id = final_signals.analysis_result_id
WHERE final_signals.is_current = TRUE
  AND final_signals.is_published = TRUE;

DROP VIEW IF EXISTS api.signal_detail;
CREATE VIEW api.signal_detail AS
SELECT
    final_signals.*,
    stocks.ticker,
    stocks.name,
    stocks.market,
    stocks.sector,
    analysis_results.analysis_date,
    analysis_results.analysis_mode,
    analysis_results.run_key AS analysis_run_key,
    analysis_results.version AS analysis_version,
    analysis_results.base_score,
    analysis_results.warning AS analysis_warning,
    analysis_results.source_signal_event_ids,
    COALESCE(agent_results.items, '[]'::JSONB) AS agent_results,
    COALESCE(signal_events.items, '[]'::JSONB) AS signal_events
FROM final_signals
INNER JOIN stocks
    ON stocks.id = final_signals.stock_id
INNER JOIN analysis_results
    ON analysis_results.id = final_signals.analysis_result_id
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id', agent_results.id,
            'debate_method', agent_results.debate_method,
            'method_score', agent_results.method_score,
            'method_signal', agent_results.method_signal,
            'method_detail', agent_results.method_detail,
            'source_signal_event_ids', agent_results.source_signal_event_ids,
            'reliability_score', agent_results.reliability_score,
            'evidence_quality', agent_results.evidence_quality,
            'llm_model', agent_results.llm_model,
            'prompt_ver', agent_results.prompt_ver,
            'created_at', agent_results.created_at
        )
        ORDER BY agent_results.debate_method, agent_results.id
    ) AS items
    FROM agent_results
    WHERE agent_results.result_id = analysis_results.id
) agent_results ON TRUE
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id', signal_events.id,
            'source_document_id', signal_events.source_document_id,
            'source_type', signal_events.source_type,
            'event_type', signal_events.event_type,
            'event_date', signal_events.event_date,
            'signal_direction', signal_events.signal_direction,
            'impact_level', signal_events.impact_level,
            'title', signal_events.title,
            'summary', signal_events.summary,
            'evidence_url', signal_events.evidence_url,
            'needs_review', signal_events.needs_review,
            'source_name', source_documents.source_name,
            'source_url', source_documents.source_url,
            'is_official', source_documents.is_official
        )
        ORDER BY
            CASE signal_events.impact_level
                WHEN 'high' THEN 0
                WHEN 'medium' THEN 1
                ELSE 2
            END,
            signal_events.event_date DESC,
            signal_events.id ASC
    ) AS items
    FROM signal_events
    LEFT JOIN source_documents
        ON source_documents.id = signal_events.source_document_id
    WHERE signal_events.id = ANY(analysis_results.source_signal_event_ids)
) signal_events ON TRUE
WHERE final_signals.is_current = TRUE
  AND final_signals.is_published = TRUE;

-- signal_backend 롤이 있으면(컷오버 환경) 읽기 권한 재부여.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT ON api.signals_current TO signal_backend;
        GRANT SELECT ON api.signal_detail TO signal_backend;
    END IF;
END $$;
