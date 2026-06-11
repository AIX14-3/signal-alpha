-- 005_collection_datalab.sql
-- Zone C (Collection/DataLab): 카테고리 기반 수집.
-- DataLab은 종목이 아닌 카테고리(검색 테마) 단위로 수집한다. 원본 문서는
-- datalab_raw_documents에, 상세는 datalab_raw_details에 category_id로 저장하고,
-- Normalizer가 datalab_category_stocks 매핑으로 카테고리 → 종목을 해석한다.

CREATE TABLE datalab_categories (
    id          BIGSERIAL    PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    sector      VARCHAR(100),
    description TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_datalab_categories_active
    ON datalab_categories (is_active)
    WHERE is_active = TRUE;

CREATE TABLE datalab_category_stocks (
    category_id BIGINT       NOT NULL REFERENCES datalab_categories(id) ON DELETE CASCADE,
    stock_id    BIGINT       NOT NULL REFERENCES stocks(id),
    weight      NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (category_id, stock_id)
);

CREATE INDEX idx_datalab_cat_stocks_stock
    ON datalab_category_stocks (stock_id);

CREATE TABLE datalab_category_keywords (
    category_id   BIGINT       NOT NULL REFERENCES datalab_categories(id) ON DELETE CASCADE,
    keyword       VARCHAR(200) NOT NULL,
    keyword_group VARCHAR(100) NOT NULL,
    source        VARCHAR(50)  NOT NULL DEFAULT 'reviewed',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (category_id, keyword)
);

CREATE INDEX idx_datalab_category_keywords_active
    ON datalab_category_keywords (category_id, is_active)
    WHERE is_active = TRUE;

CREATE TABLE datalab_raw_documents (
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

CREATE INDEX idx_datalab_raw_doc_category
    ON datalab_raw_documents (category_id, published_at DESC);

CREATE INDEX idx_datalab_raw_doc_run
    ON datalab_raw_documents (collector_run_id)
    WHERE collector_run_id IS NOT NULL;

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

CREATE INDEX idx_datalab_category_date
    ON datalab_raw_details (category_id, observed_date DESC);

CREATE INDEX idx_datalab_spike
    ON datalab_raw_details (category_id, is_spike)
    WHERE is_spike = TRUE;
