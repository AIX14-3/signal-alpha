-- 0005_api_read_contract.sql
-- target: all
-- ============================================================================
-- api 스키마 읽기 계약 view (2-인스턴스 분리 재베이스라인)
-- ----------------------------------------------------------------------------
-- 배경: backend(main-server)는 worker 산출물을 base 테이블 직접 권한 없이 `api.*`
--   읽기전용 view 로만 조회한다. view 는 PUBLISHED 테이블(final_signals/stocks/
--   analysis_results/agent_results/signal_events/source_documents) 위에서만 JOIN 하므로
--   수집 DB(워커가 base 기록)·백엔드 DB(publisher 사본) 양쪽에서 self-contained 하게 동작한다.
-- 설계:
--   - view 는 소유자(DB owner) 권한으로 base 를 읽어, signal_backend 롤은 base 권한 없이도 조회.
--   - return 채널 컬럼(ml_final_score/ml_direction/ml_confidence)은 final_signals 에 포함되어
--     `final_signals.*` 로 자동 노출된다(0002_published_baseline 에서 컬럼 생성).
--   - api 스키마 SELECT grant 는 0007_backend_grants(target backend)에서 부여한다.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS api;

-- api.signals_current — 현재 발행 시그널 목록 행.
CREATE OR REPLACE VIEW api.signals_current AS
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

-- api.signal_detail — 시그널 상세(agent_results / signal_events JSONB 집계 포함).
CREATE OR REPLACE VIEW api.signal_detail AS
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

-- api.stocks — 종목 마스터 읽기면.
CREATE OR REPLACE VIEW api.stocks AS
SELECT
    id,
    ticker,
    name,
    market,
    sector,
    is_active,
    created_at,
    updated_at
FROM stocks;

-- 주: api.analysis_pipeline_status 는 processing_queue(COLLECTION) 의존이라 수집 DB 에만 존재한다
--   → 0005b_api_pipeline_status_collection.sql(target collection) 로 분리.
