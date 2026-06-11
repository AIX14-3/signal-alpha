CREATE TABLE IF NOT EXISTS dart_collection_states (
    stock_id BIGINT PRIMARY KEY REFERENCES stocks(id) ON DELETE CASCADE,
    ticker VARCHAR(10) NOT NULL,
    last_bgn_de DATE NOT NULL,
    last_end_de DATE NOT NULL,
    last_receipt_no VARCHAR(30),
    last_collected_count INTEGER NOT NULL DEFAULT 0,
    last_collector_run_id BIGINT REFERENCES collector_runs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dart_collection_states_ticker
    ON dart_collection_states (ticker);

CREATE INDEX IF NOT EXISTS idx_dart_collection_states_end_de
    ON dart_collection_states (last_end_de DESC);
