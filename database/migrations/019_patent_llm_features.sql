-- 019_patent_llm_features.sql
--
-- Patent scoring currently counts filings (volume/momentum/new-category) but is
-- blind to whether a filing is a core, high-impact invention or routine noise.
-- C3 adds an LLM ENRICHMENT cache: a separate enrichment tool (NOT the collector,
-- NOT the analyzer) reads each patent's title + abstract once and stores structured
-- significance features here. The analyzer then reads the cached features to let
-- important patents drive the score, and falls back to the count-based rules when
-- a patent has not been enriched.
--
-- application_no is immutable, so enrichment is a one-time job per patent:
--   llm_status 'pending'  → not yet enriched (default for all existing/new rows)
--   llm_status 'success'  → llm_features populated and validated
--   llm_status 'failed'   → enrichment attempted but unusable (analyzer treats as not enriched)
--   llm_status 'skipped'  → no abstract/title to enrich (permanent, do not retry)

ALTER TABLE patent_raw_details
    ADD COLUMN llm_features JSONB,
    ADD COLUMN llm_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (llm_status IN ('pending', 'success', 'failed', 'skipped'));

-- Drives the enrichment worklist query:
--   WHERE llm_status='pending' ORDER BY application_date DESC, raw_document_id DESC
-- A PARTIAL index on the 'pending' subset only — it shrinks toward empty as
-- enrichment completes (most rows end up 'success'), instead of a full index that
-- keeps indexing every already-enriched row. The key columns also match the
-- ORDER BY, so the worklist read is an index-only range scan with no extra sort.
CREATE INDEX idx_patent_llm_pending
    ON patent_raw_details (application_date DESC, raw_document_id DESC)
    WHERE llm_status = 'pending';
