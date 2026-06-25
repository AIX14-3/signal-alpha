-- Structured valuation facts extracted from broker report PDFs.
-- New report code should use the canonical raw_documents -> report_raw_details
-- -> report_chunks/report_valuation_facts path, not legacy report_raw tables.

CREATE TABLE report_valuation_facts (
    raw_document_id BIGINT PRIMARY KEY,
    stock_id BIGINT NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    broker VARCHAR(100),
    analyst VARCHAR(100),
    publish_date DATE,
    target_price INTEGER,
    forward_eps_est INTEGER,
    eps_fy INTEGER,
    methodology VARCHAR(20) NOT NULL DEFAULT 'unknown'
        CHECK (methodology IN ('PER', 'PBR', 'EV_EBITDA', 'SOTP', 'DCF', 'mixed', 'unknown')),
    applied_multiple NUMERIC(12, 4),
    implied_multiple NUMERIC(12, 4),
    peer_group JSONB NOT NULL DEFAULT '[]'::jsonb,
    category_tag VARCHAR(80),
    rerating_thesis TEXT,
    extraction_source VARCHAR(30) NOT NULL DEFAULT 'rules'
        CHECK (extraction_source IN ('rules', 'llm', 'rules_fallback')),
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_report_valuation_stock_publish
    ON report_valuation_facts (stock_id, publish_date DESC);

CREATE INDEX idx_report_valuation_methodology
    ON report_valuation_facts (methodology, stock_id);

CREATE INDEX idx_report_valuation_needs_review
    ON report_valuation_facts (needs_review, stock_id)
    WHERE needs_review = TRUE;

CREATE TRIGGER trg_report_valuation_facts_updated_at
BEFORE UPDATE ON report_valuation_facts
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();
