-- 017_alternative_signals_consensus.sql
--
-- Alternative Signal aggregation realignment to docs/project-context.md §10: the
-- dashboard output should expose a "consensus_score" + "alignment_rate" and
-- separate "positive_evidence" / "caution_evidence", not just a single weighted
-- score and a "confidence" number (the doc explicitly avoids the word
-- "confidence" because it reads like investment-grade confidence).
--
-- Columns are additive on the existing `final_signals` table (the aggregated
-- output row); `confidence`/`final_score` are kept for backward compatibility.
-- `consensus_score` carries the alignment/coverage strength (0-100) and the two
-- evidence arrays hold plain human/LLM-readable strings.
--
-- NOTE: the dashboard's "alignment_rate" is NOT stored here. Its value is always
-- identical to the existing `source_agreement` column (HIGH/MEDIUM/LOW) — see
-- AlternativeAggregator.merge — so storing it again would be a redundant,
-- update-anomaly-prone duplicate. The read/API layer aliases source_agreement as
-- alignment_rate (the schema exposes it as a derived property). Single source of
-- truth: source_agreement.

ALTER TABLE final_signals
    ADD COLUMN consensus_score   NUMERIC(5,2) CHECK (consensus_score BETWEEN 0 AND 100),
    ADD COLUMN positive_evidence JSONB,
    ADD COLUMN caution_evidence  JSONB;
