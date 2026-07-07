-- 20260708_0900_grant_worker_subscription_read.sql
-- target: backend
-- ============================================================================
-- 매매 부검 — 체결 동기화 워커의 구독 게이팅용 읽기 권한 (코드리뷰 M2)
-- ----------------------------------------------------------------------------
-- 체결 동기화 러너(run_trade_fills_sync)가 구독 해지 후에도 active 자격증명을 계속
--   동기화하던 게이팅 갭 수정. list_sync_targets 가 signal_subscriptions 를 EXISTS 로
--   조인해 활성 구독 유저만 대상으로 삼는다 — 그러려면 signal_worker 에 그 테이블 SELECT 가
--   필요하다(워커는 이미 users/stocks 를 읽음, 읽기 전용 게이팅이라 결합 리스크 낮음).
-- 이 마이그는 grant 만 부여한다(스키마 변경 없음 — schema.sql/db_partition 무관, --no-privileges 덤프).
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT ON public.signal_subscriptions TO signal_worker;
    END IF;
END $$;
