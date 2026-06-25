-- 20260625_1343_api_schema_read_contract.sql
-- ============================================================================
-- api schema read contract
-- ----------------------------------------------------------------------------
-- 배경: worker(agent-worker)와 backend(main-server)가 같은 Postgres를 공유하지만,
--   배포는 db/worker/backend/frontend 4유닛으로 분리한다. backend가 worker 산출
--   테이블(final_signals 등)을 직접 읽으면 worker 내부 스키마에 결합되고, 권한
--   격리도 어렵다. 그래서 backend가 읽는 worker 데이터를 `api` 스키마의 안정적인
--   읽기전용 view로만 노출한다(읽기 계약). backend 롤은 base 테이블 권한 없이
--   이 view만 SELECT한다(권한은 db_roles_and_grants 마이그레이션에서 부여).
-- 설계:
--   - view는 소유자(마이그레이션 실행 롤=DB owner) 권한으로 base 테이블을 읽는다.
--     따라서 backend 롤은 final_signals 직접 권한 없이도 view로 조회 가능.
--   - 각 view는 기존 repository 쿼리가 SELECT하던 컬럼셋을 그대로 재현한다.
--     is_current / is_published 필터를 view 안에 내장하고, 요청별 WHERE/ORDER/LIMIT는
--     호출 repository가 유지한다.
--   - 깨는 변경 시 api.<name>_v2 를 추가하고 소비처를 이전한 뒤 구 view를 제거한다.
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

CREATE SCHEMA IF NOT EXISTS api;

-- ----------------------------------------------------------------------------
-- api.signals_current
--   SignalRepository.get_current_by_ticker / list_current_published /
--   list_current_by_stock_ids 가 읽던 "현재 발행 시그널" 행.
--   final_signals.* + stocks(ticker/name/market/sector) + analysis_results 요약.
-- ----------------------------------------------------------------------------
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

-- ----------------------------------------------------------------------------
-- api.signal_detail
--   SignalRepository.get_detail_by_id 가 읽던 시그널 상세.
--   agent_results / signal_events 를 JSONB로 집계해 함께 제공한다.
-- ----------------------------------------------------------------------------
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

-- ----------------------------------------------------------------------------
-- api.stocks
--   종목 마스터 참조 읽기면. StockRepository 읽기 + watchlists/journals/queue 의
--   INNER JOIN stocks 가 사용한다. (쓰기 ensure_stock 은 worker 전용으로 base 테이블 사용)
-- ----------------------------------------------------------------------------
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

-- ----------------------------------------------------------------------------
-- api.analysis_pipeline_status
--   ProcessingQueueRepository.list_tasks (analytics 폴링)용. 내부 에러/컨텍스트
--   페이로드는 노출하지 않고 단계 표시에 필요한 컬럼만 제공한다.
-- ----------------------------------------------------------------------------
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
