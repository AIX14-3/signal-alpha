-- 20260702_0900_collection_schedule_policy.sql
-- target: backend
-- ============================================================================
-- Scheduler-agent per-schedule policy knobs.
-- ============================================================================

ALTER TABLE public.collection_schedules
    ADD COLUMN report_limit integer NOT NULL DEFAULT 100,
    ADD COLUMN report_days_back integer NOT NULL DEFAULT 7,
    ADD COLUMN report_max_pages integer NOT NULL DEFAULT 20,
    ADD COLUMN alternative_collect_enabled boolean NOT NULL DEFAULT true,
    ADD COLUMN alternative_analyze_enabled boolean NOT NULL DEFAULT true,
    ADD COLUMN alternative_collect_timeout_seconds integer NOT NULL DEFAULT 3600,
    ADD COLUMN alternative_analyze_timeout_seconds integer NOT NULL DEFAULT 3600,
    ADD COLUMN backpressure_max_waiting integer,
    ADD COLUMN backpressure_max_failed integer;

ALTER TABLE public.collection_schedules
    ADD CONSTRAINT collection_schedules_report_limit_check
        CHECK (report_limit >= 1 AND report_limit <= 1000),
    ADD CONSTRAINT collection_schedules_report_days_back_check
        CHECK (report_days_back >= 1 AND report_days_back <= 400),
    ADD CONSTRAINT collection_schedules_report_max_pages_check
        CHECK (report_max_pages >= 1 AND report_max_pages <= 200),
    ADD CONSTRAINT collection_schedules_alternative_collect_timeout_check
        CHECK (
            alternative_collect_timeout_seconds >= 60
            AND alternative_collect_timeout_seconds <= 86400
        ),
    ADD CONSTRAINT collection_schedules_alternative_analyze_timeout_check
        CHECK (
            alternative_analyze_timeout_seconds >= 60
            AND alternative_analyze_timeout_seconds <= 86400
        ),
    ADD CONSTRAINT collection_schedules_backpressure_max_waiting_check
        CHECK (backpressure_max_waiting IS NULL OR backpressure_max_waiting >= 0),
    ADD CONSTRAINT collection_schedules_backpressure_max_failed_check
        CHECK (backpressure_max_failed IS NULL OR backpressure_max_failed >= 0);

UPDATE public.collection_schedules
SET report_limit = 100,
    report_days_back = 7,
    report_max_pages = 20,
    alternative_collect_enabled = true,
    alternative_analyze_enabled = true,
    alternative_collect_timeout_seconds = 3600,
    alternative_analyze_timeout_seconds = 3600,
    backpressure_max_waiting = NULL,
    backpressure_max_failed = NULL,
    updated_by = COALESCE(updated_by, 'migration:collection-schedule-policy'),
    updated_at = NOW();
