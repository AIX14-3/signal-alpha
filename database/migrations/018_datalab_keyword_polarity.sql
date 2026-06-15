-- 018_datalab_keyword_polarity.sql
--
-- DataLab search volume is direction-blind: a surge in "{brand}" searches can be
-- demand (good) OR a scandal/recall (bad). To recover polarity, each keyword is
-- tagged as a demand signal, a risk signal, or neutral. The analyzer then scores
-- demand-up as positive and risk-up as negative, instead of "any rise = bullish".
--
-- Default 'demand' preserves current behaviour for all existing keywords (rising
-- = positive); only newly added risk keywords (e.g. "{brand} 리콜/불매/논란") flip
-- the sign once they are collected.

ALTER TABLE datalab_category_keywords
    ADD COLUMN polarity VARCHAR(10) NOT NULL DEFAULT 'demand'
        CHECK (polarity IN ('demand', 'risk', 'neutral'));

-- No dedicated index: polarity is only ever SELECTed alongside a keyword row
-- (COALESCE(dck.polarity,'demand')), never used as a filter predicate, and the
-- table's PRIMARY KEY (category_id, keyword) already serves the category_id join.
-- A (category_id, polarity) index would be pure write overhead on a 3-value,
-- tiny-per-category column. Add one only if a polarity-filtered query appears.
