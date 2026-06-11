CREATE TABLE IF NOT EXISTS datalab_categories (
    id          BIGSERIAL    PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    sector      VARCHAR(100),
    description TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_datalab_categories_active
    ON datalab_categories (is_active)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS datalab_category_stocks (
    category_id BIGINT       NOT NULL REFERENCES datalab_categories(id) ON DELETE CASCADE,
    stock_id    BIGINT       NOT NULL REFERENCES stocks(id),
    weight      NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (category_id, stock_id)
);

CREATE INDEX IF NOT EXISTS idx_datalab_cat_stocks_stock
    ON datalab_category_stocks (stock_id);

CREATE TABLE IF NOT EXISTS datalab_raw_documents (
    id               BIGSERIAL    PRIMARY KEY,
    category_id      BIGINT       NOT NULL REFERENCES datalab_categories(id),
    collector_run_id BIGINT       REFERENCES collector_runs(id),
    source_name      VARCHAR(100) NOT NULL,
    external_id      VARCHAR(500) NOT NULL,
    source_hash      VARCHAR(64)  NOT NULL UNIQUE,
    title            TEXT         NOT NULL,
    source_url       TEXT,
    published_at     TIMESTAMPTZ  NOT NULL,
    collect_status   VARCHAR(20)  NOT NULL DEFAULT 'success'
        CHECK (collect_status IN ('success', 'partial', 'failed')),
    collect_error    TEXT,
    collected_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    collector_ver    VARCHAR(20)  NOT NULL DEFAULT '1.0',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_datalab_raw_doc UNIQUE (source_name, external_id)
);

CREATE INDEX IF NOT EXISTS idx_datalab_raw_doc_category
    ON datalab_raw_documents (category_id, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_datalab_raw_doc_run
    ON datalab_raw_documents (collector_run_id)
    WHERE collector_run_id IS NOT NULL;
