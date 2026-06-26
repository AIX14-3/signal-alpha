-- Signal Journal MVP policy.
-- User-facing journal values avoid investment-action wording.

UPDATE signal_journals
SET user_view = 'watch'
WHERE user_view IN ('bullish', 'bearish', 'neutral');

ALTER TABLE signal_journals
    DROP CONSTRAINT signal_journals_user_view_check;

ALTER TABLE signal_journals
    ADD CONSTRAINT signal_journals_user_view_check
    CHECK (user_view IN ('watch', 'research_more', 'not_relevant'));

ALTER TABLE signal_journals
    ADD COLUMN tags JSONB NOT NULL DEFAULT '[]'::JSONB;
