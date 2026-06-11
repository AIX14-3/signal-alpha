-- hiring_baseline: 네이버 DataLab 기반 채용 트렌드 계절성 기준선
-- 분기별 가중치(qX_factor) × 평균 검색량(avg_search_volume) = seasonal_baseline
-- DataLabBaselineCollector가 분기 1회 UPSERT, HiringCollector가 읽기 전용으로 참조

CREATE TABLE IF NOT EXISTS hiring_baseline (
    id                 BIGSERIAL PRIMARY KEY,
    stock_id           BIGINT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    avg_search_volume  NUMERIC(10,2) NOT NULL DEFAULT 0,
    q1_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    q2_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    q3_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    q4_factor          NUMERIC(5,3)  NOT NULL DEFAULT 1.0,
    keyword_group_name VARCHAR(200),
    data_start_date    DATE,
    data_end_date      DATE,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hiring_baseline_stock UNIQUE (stock_id)
);

CREATE INDEX IF NOT EXISTS idx_hiring_baseline_stock
    ON hiring_baseline (stock_id);
