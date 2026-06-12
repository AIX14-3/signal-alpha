-- 004_collection_dart.sql
-- Zone C (Collection/DART): DART 고유번호 매핑 + 종목별 수집 상태.

CREATE TABLE dart_corp_codes (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL UNIQUE,
    ticker VARCHAR(10) NOT NULL,
    corp_name VARCHAR(200) NOT NULL,
    corp_name_eng VARCHAR(200),
    stock_name VARCHAR(200),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dart_corp_ticker UNIQUE (ticker)
);

CREATE INDEX idx_dart_corp_codes_stock
    ON dart_corp_codes (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX idx_dart_corp_codes_ticker
    ON dart_corp_codes (ticker);

CREATE TABLE dart_collection_states (
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

CREATE UNIQUE INDEX uq_dart_collection_states_ticker
    ON dart_collection_states (ticker);

CREATE INDEX idx_dart_collection_states_end_de
    ON dart_collection_states (last_end_de DESC);
