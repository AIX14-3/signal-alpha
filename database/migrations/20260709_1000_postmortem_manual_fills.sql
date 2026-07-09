-- 20260709_1000_postmortem_manual_fills.sql
-- target: backend
-- ============================================================================
-- 매매 부검 — 증권사 API 연동 제거 → 수기 입력 전환
-- ----------------------------------------------------------------------------
-- 유저가 증권사(키움/토스) API 키를 등록해 체결을 자동 동기화하던 방식을 걷어내고, 매수/매도
--   체결을 유저가 직접(수기) 입력한다. 자격증명 테이블(user_broker_credentials)과 그 온디맨드
--   동기화 신호(sync_requested_at 컬럼)는 전부 불필요 → forward drop(revert 대신 삭제 마이그).
-- user_trade_fills 는 부검의 유일 데이터원으로 유지하되, 브로커 전용 컬럼(broker·account_ref·
--   broker_fill_id)과 그에 걸린 제약/인덱스를 제거한다. 수기 입력 행은 identity(id)로 식별하며
--   브로커 체결ID 같은 별도 자연키가 없다(멱등 재동기화 개념이 사라짐 → 중복은 유저가 삭제).
-- 권한: 수기 입력/삭제는 main-server(signal_backend) 몫 → INSERT 부여(SELECT/DELETE 는 기존
--   grant 유지). 워커는 더 이상 체결을 적재하지 않고 overlay 산출용 SELECT 만 → INSERT 회수.
-- ============================================================================

-- 1) 자격증명 테이블 제거(연동 기능 삭제). 의존 객체(시퀀스/인덱스/트리거/제약) 함께 정리.
DROP TABLE IF EXISTS public.user_broker_credentials CASCADE;

-- 2) user_trade_fills 에서 브로커 전용 구조 제거 -------------------------------
ALTER TABLE public.user_trade_fills DROP CONSTRAINT IF EXISTS uq_trade_fill;
ALTER TABLE public.user_trade_fills DROP CONSTRAINT IF EXISTS chk_trade_fill_broker;
DROP INDEX IF EXISTS public.idx_trade_fills_user_broker;

ALTER TABLE public.user_trade_fills
    DROP COLUMN IF EXISTS broker,
    DROP COLUMN IF EXISTS account_ref,
    DROP COLUMN IF EXISTS broker_fill_id;

-- 패턴 부검(전체 시간순) — 브로커 축이 사라진 자리의 유저별 시간순 조회 인덱스.
CREATE INDEX idx_trade_fills_user_time ON public.user_trade_fills (user_id, filled_at DESC);

-- 3) 권한 재배치 (멱등, 롤 존재 시에만) ---------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT INSERT ON public.user_trade_fills TO signal_backend;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        REVOKE INSERT ON public.user_trade_fills FROM signal_worker;
    END IF;
END $$;
