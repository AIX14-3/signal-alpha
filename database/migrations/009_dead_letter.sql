-- 005_dead_letter.sql
-- ============================================================================
-- Phase 2 DLQ — 큐 태스크 종착 실패 격리 + replay (이슈 #139)
-- ----------------------------------------------------------------------------
-- 배경
--   processing_queue 태스크가 max_retry 초과(QueueTaskRunner) 또는 timeout
--   (sweep_stale_active_tasks)으로 status='failed'에 종착하면 그대로 큐에 누적되기만
--   하고, 실패를 격리·분석·수동 재시도(replay)할 전용 경로가 없다.
--
-- 설계
--   dead_letter는 종착 실패 태스크의 "아카이브 + replay 원장"이다.
--     - replay에 필요한 processing_queue 컬럼을 미러
--       (stock_id, task_type, priority, source_*_ids, task_context).
--     - processing_queue_id UNIQUE → 같은 태스크를 두 번 아카이브하지 않는다(멱등).
--       runner 훅(즉시) + reconcile 백스탑(sweep가 만든 failed)이 동시에 INSERT를
--       시도해도 ON CONFLICT (processing_queue_id) DO NOTHING으로 안전.
--     - replayed_at / replayed_task_id 로 재등록 이력을 남긴다.
--   stock_id는 NULL 허용 — DataLab 태스크는 카테고리 단위라 stock_id가 없다
--   (processing_queue.stock_id와 동일).
-- ============================================================================

CREATE TABLE dead_letter (
    id BIGSERIAL PRIMARY KEY,
    processing_queue_id BIGINT NOT NULL UNIQUE REFERENCES processing_queue(id),
    stock_id BIGINT REFERENCES stocks(id),
    task_type VARCHAR(50) NOT NULL,
    priority VARCHAR(10) NOT NULL DEFAULT 'batch'
        CHECK (priority IN ('immediate', 'batch')),
    source_raw_ids BIGINT[],
    source_signal_event_ids BIGINT[],
    source_analysis_result_ids BIGINT[],
    task_context JSONB,
    final_error_message TEXT,
    final_retry_count SMALLINT NOT NULL DEFAULT 0,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    replayed_at TIMESTAMPTZ,
    replayed_task_id BIGINT REFERENCES processing_queue(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dead_letter_task_type
    ON dead_letter (task_type, archived_at DESC);

-- 아직 재시도 안 한 실패만 빠르게 — 대시보드/replay 대상 조회용 부분 인덱스.
CREATE INDEX idx_dead_letter_unreplayed
    ON dead_letter (archived_at DESC)
    WHERE replayed_at IS NULL;
