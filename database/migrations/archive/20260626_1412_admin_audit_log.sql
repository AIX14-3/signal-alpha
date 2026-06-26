-- 20260626_1412_admin_audit_log.sql
-- ============================================================================
-- admin_audit_log — 관리자 회원/구독/결제 변경 감사 로그
-- ----------------------------------------------------------------------------
-- 배경: 관리자가 회원을 추가/수정/삭제하거나 구독을 변경하고 결제를 환불해도
--   "누가 언제 무엇을 어떻게 바꿨는지" 기록이 남지 않았다. 분쟁/대사/책임추적이
--   불가능했고, 프론트의 변경이 DB에 흔적 없이 사라지는 문제의 일부이기도 하다.
-- 설계: 변경 1건당 1행. before/after 를 JSONB 스냅샷으로 남겨 어떤 필드가 바뀌었는지
--   재구성 가능하게 한다. actor_admin_id 는 admin_accounts 참조(시스템 변경은 NULL).
--   target_type/target_id 로 대상(user/subscription/payment 등)을 식별.
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_admin_id BIGINT REFERENCES admin_accounts(id),
    action VARCHAR(50) NOT NULL,          -- 예: user.create / user.update / user.delete / subscription.set / payment.refund
    target_type VARCHAR(30) NOT NULL,     -- 예: user / subscription / payment
    target_id BIGINT,
    before JSONB,
    after JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_target
    ON admin_audit_log (target_type, target_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_actor
    ON admin_audit_log (actor_admin_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- 백엔드 롤 권한. 롤이 아직 없으면(로컬/dev) 건너뛴다.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.admin_audit_log TO signal_backend;
        GRANT USAGE, SELECT ON SEQUENCE admin_audit_log_id_seq TO signal_backend;
    END IF;
END $$;
