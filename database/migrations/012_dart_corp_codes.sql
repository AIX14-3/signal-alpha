CREATE TABLE IF NOT EXISTS dart_corp_codes (
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

CREATE INDEX IF NOT EXISTS idx_dart_corp_codes_stock
    ON dart_corp_codes (stock_id)
    WHERE stock_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dart_corp_codes_ticker
    ON dart_corp_codes (ticker);
