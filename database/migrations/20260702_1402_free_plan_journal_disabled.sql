-- 20260702_1402_free_plan_journal_disabled.sql
-- target: backend
-- ============================================================================
-- free 플랜 저널 미제공 (저널 = 구독 전용 전환)
-- ----------------------------------------------------------------------------
-- 배경: 저널 기능이 전체 구독 전용으로 전환됨(/api/journals* 전부 402
--   SUBSCRIPTION_REQUIRED). free 플랜의 journal_max_entries=50 은 과거 "무료 50건"
--   기획의 잔재라 0(미제공)으로 정정한다. 컬럼 자체는 plans API 응답/프론트 Plan
--   타입이 노출 중이므로 유지. seeds/002 도 동일 값으로 정정(fresh DB 정본).
-- ============================================================================

UPDATE public.subscription_plans
SET journal_max_entries = 0
WHERE plan_type = 'free';
