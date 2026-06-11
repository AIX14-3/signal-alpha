CREATE TABLE IF NOT EXISTS processing_queue (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    task_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'retrying', 'skipped')),
    priority VARCHAR(10) NOT NULL DEFAULT 'batch'
        CHECK (priority IN ('immediate', 'batch')),
    source_raw_ids BIGINT[],
    source_signal_event_ids BIGINT[],
    source_analysis_result_ids BIGINT[],
    task_context JSONB,
    retry_count SMALLINT NOT NULL DEFAULT 0,
    max_retry_count SMALLINT NOT NULL DEFAULT 3,
    error_message TEXT,
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_queue_pending
    ON processing_queue (task_type, priority, scheduled_at ASC)
    WHERE status IN ('pending', 'retrying');

CREATE INDEX IF NOT EXISTS idx_queue_immediate
    ON processing_queue (stock_id, scheduled_at ASC)
    WHERE priority = 'immediate' AND status = 'pending';

CREATE INDEX IF NOT EXISTS idx_queue_raw_ids
    ON processing_queue USING GIN (source_raw_ids);

CREATE INDEX IF NOT EXISTS idx_queue_signal_event_ids
    ON processing_queue USING GIN (source_signal_event_ids);

CREATE INDEX IF NOT EXISTS idx_queue_analysis_result_ids
    ON processing_queue USING GIN (source_analysis_result_ids);

CREATE TABLE IF NOT EXISTS source_documents (
    id BIGSERIAL PRIMARY KEY,
    raw_document_id BIGINT NOT NULL UNIQUE,
    stock_id BIGINT NOT NULL,
    source_type VARCHAR(20) NOT NULL
        CHECK (source_type IN ('DART', 'REPORT', 'HIRING', 'PATENT', 'DATALAB')),
    source_name VARCHAR(100) NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    reliability_level VARCHAR(10) NOT NULL DEFAULT 'medium'
        CHECK (reliability_level IN ('high', 'medium', 'low')),
    is_official BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_doc_stock
    ON source_documents (stock_id, source_type, published_at DESC);

CREATE TABLE IF NOT EXISTS signal_events (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    source_document_id BIGINT NOT NULL REFERENCES source_documents(id),
    event_hash VARCHAR(64) NOT NULL UNIQUE,
    source_type VARCHAR(20) NOT NULL
        CHECK (source_type IN ('DART', 'REPORT', 'HIRING', 'PATENT', 'DATALAB')),
    event_type VARCHAR(50) NOT NULL,
    event_date DATE NOT NULL,
    signal_direction VARCHAR(10) NOT NULL
        CHECK (signal_direction IN ('positive', 'negative', 'neutral', 'mixed', 'unknown')),
    impact_level VARCHAR(10) NOT NULL
        CHECK (impact_level IN ('high', 'medium', 'low')),
    title TEXT NOT NULL,
    summary TEXT,
    evidence_text TEXT,
    evidence_url TEXT,
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS signal_metrics (
    id BIGSERIAL PRIMARY KEY,
    signal_event_id BIGINT NOT NULL REFERENCES signal_events(id) ON DELETE CASCADE,
    metric_name VARCHAR(50) NOT NULL,
    metric_value NUMERIC(15,4) NOT NULL,
    metric_unit VARCHAR(20),
    previous_value NUMERIC(15,4),
    change_pct NUMERIC(8,2),
    period_start DATE,
    period_end DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_signal_metric UNIQUE (signal_event_id, metric_name)
);

CREATE TABLE IF NOT EXISTS validation_logs (
    id BIGSERIAL PRIMARY KEY,
    target_type VARCHAR(30) NOT NULL
        CHECK (target_type IN (
            'signal_event',
            'signal_metric',
            'analysis_result',
            'agent_result',
            'final_signal',
            'llm_output'
        )),
    target_id_int BIGINT,
    target_id_uuid UUID,
    validation_type VARCHAR(50) NOT NULL,
    passed BOOLEAN NOT NULL,
    message TEXT,
    retry_count SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_validation_target_id
        CHECK (num_nonnulls(target_id_int, target_id_uuid) = 1)
);
