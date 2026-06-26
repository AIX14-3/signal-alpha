-- 20260626_1600_api_views_return_channel_refresh.sql
-- target: all
-- ============================================================================
-- api.signals_current / api.signal_detail 갱신 — return 채널 컬럼 노출 (#525 WS-C)
-- ----------------------------------------------------------------------------
-- 배경
--   두 view 는 ``final_signals.*`` 로 정의됐는데, view 생성 시점(20260625_1343)이
--   final_signals return 채널 컬럼 추가(20260626_1530: ml_final_score/ml_direction/
--   ml_confidence)보다 앞서서 ``*`` 가 옛 컬럼셋으로 **고정**됐다. 그 결과 백엔드가
--   api.signals_current 로 ml_* 를 읽지 못해 프론트 return 채널이 항상 NULL 이 된다.
-- 설계
--   - CREATE OR REPLACE 는 컬럼 재정렬을 못 하므로(ml_* 가 뒤따르는 stocks/analysis 컬럼
--     앞에 삽입됨) DROP + CREATE 로 ``*`` 를 재전개한다. 정의는 원본과 동일.
--   - 멱등: DROP VIEW IF EXISTS. signal_backend 롤 존재 시 SELECT 재부여.
--   - target: all — 단일 DB(현행)와 백엔드 DB(부트스트랩) 양쪽의 api 스키마에 적용.
-- ============================================================================

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
