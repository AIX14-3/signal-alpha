CREATE TABLE IF NOT EXISTS datalab_category_keywords (
    category_id   BIGINT       NOT NULL REFERENCES datalab_categories(id) ON DELETE CASCADE,
    keyword       VARCHAR(200) NOT NULL,
    keyword_group VARCHAR(100) NOT NULL,
    source        VARCHAR(50)  NOT NULL DEFAULT 'reviewed',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (category_id, keyword)
);

CREATE INDEX IF NOT EXISTS idx_datalab_category_keywords_active
    ON datalab_category_keywords (category_id, is_active)
    WHERE is_active = TRUE;

ALTER TABLE datalab_category_keywords ENABLE ROW LEVEL SECURITY;
