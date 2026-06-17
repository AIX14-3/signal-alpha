-- Migration 014: datalab_category_keywords.polarity_source 기본값 'manual' → 'default'
-- 목적: 미태깅 키워드를 로더가 COALESCE(..., 'default')로 다루는 런타임 동작과 DB 기본값을
--       일치시킨다. 007의 DEFAULT 'manual'은 '사람 태깅'을 의미해 provenance가 부정확했다.
--       CHECK 제약에 'default'는 이미 포함(007)이라 변경 불필요. 기존 행 백필은
--       '진짜 manual(사람)'과 'default(폴백)'을 구분할 수 없어 수행하지 않는다.

ALTER TABLE datalab_category_keywords
    ALTER COLUMN polarity_source SET DEFAULT 'default';
