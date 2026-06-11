-- 020_datalab_raw_details_category.sql
--
-- Redefine datalab_raw_details to the category-based contract.
--
-- DataLab data is collected by category (search theme), not by stock. The raw
-- document lives in datalab_raw_documents (see 013), and the detail row must
-- reference it by category_id — NOT stock_id. The original definition (004)
-- modelled it like the stock-based detail tables (stock_id NOT NULL, FK to
-- raw_documents(id, stock_id)), which is incompatible with the collector.
--
-- The table has never held valid data under the new flow (the collector's
-- INSERT could not succeed against the old shape), so it is dropped and
-- recreated rather than migrated column-by-column.

DROP TABLE IF EXISTS datalab_raw_details CASCADE;

CREATE TABLE datalab_raw_details (
    raw_document_id       BIGINT       PRIMARY KEY
        REFERENCES datalab_raw_documents(id) ON DELETE CASCADE,
    category_id           BIGINT       NOT NULL REFERENCES datalab_categories(id),
    keyword               VARCHAR(100) NOT NULL,
    keyword_group         VARCHAR(100),
    observed_date         DATE         NOT NULL,
    search_index          NUMERIC(6,2) NOT NULL,
    previous_search_index NUMERIC(6,2),
    change_pct            NUMERIC(8,2),
    period_type           VARCHAR(10)  NOT NULL DEFAULT 'daily'
        CHECK (period_type IN ('daily', 'weekly', 'monthly')),
    device                VARCHAR(10)  NOT NULL DEFAULT 'all'
        CHECK (device IN ('pc', 'mobile', 'all')),
    gender                VARCHAR(5)   NOT NULL DEFAULT 'all'
        CHECK (gender IN ('m', 'f', 'all')),
    age_group             VARCHAR(20)  NOT NULL DEFAULT 'all',
    is_spike              BOOLEAN      NOT NULL DEFAULT FALSE,
    extra_payload         JSONB,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_datalab UNIQUE (
        category_id,
        keyword,
        observed_date,
        period_type,
        device,
        gender,
        age_group
    )
);

CREATE INDEX IF NOT EXISTS idx_datalab_category_date
    ON datalab_raw_details (category_id, observed_date DESC);

CREATE INDEX IF NOT EXISTS idx_datalab_spike
    ON datalab_raw_details (category_id, is_spike)
    WHERE is_spike = TRUE;
