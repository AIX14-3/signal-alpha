-- 20260702_1401_journal_legacy_columns_drop.sql
-- target: backend
-- ============================================================================
-- signal_journals 레거시 컬럼 제거 (forward-only)
-- ----------------------------------------------------------------------------
-- 배경: 초기 설계(매매 결정 기록) 잔재인 decision_type/decision_reason/price_at_time 은
--   018_signal_journal_mvp_policy 에서 정책이 "투자행위 표현 제거"로 바뀐 뒤 어떤 코드도
--   쓰지 않는다(런타임 참조 0 확인, 2026-07-02). 단일 시점 outcome_* 3컬럼은
--   20260702_1400 의 signal_journal_outcomes(다중 시점 테이블)로 대체되어 함께 제거.
-- 설계: 멱등 DROP(IF EXISTS). CASCADE 는 쓰지 않는다 — 의외의 의존이 있으면 조용히
--   지우지 말고 실패로 드러내기 위함.
-- ============================================================================

ALTER TABLE public.signal_journals
    DROP COLUMN IF EXISTS decision_type,
    DROP COLUMN IF EXISTS decision_reason,
    DROP COLUMN IF EXISTS price_at_time,
    DROP COLUMN IF EXISTS outcome_price,
    DROP COLUMN IF EXISTS outcome_change_pct,
    DROP COLUMN IF EXISTS outcome_checked_at;
