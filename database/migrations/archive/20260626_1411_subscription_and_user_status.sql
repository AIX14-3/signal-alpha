-- 20260626_1411_subscription_and_user_status.sql
-- ============================================================================
-- signal_subscriptions 결제일 컬럼 + users.status (회원 상태)
-- ----------------------------------------------------------------------------
-- 배경: 관리자 화면에서 구독을 수정할 때 expires_at(만료일)만 설정 가능했고,
--   다음 결제일/자동갱신 여부는 DB에 보관할 곳이 없었다. 또한 회원의 정지/탈퇴
--   상태를 deleted_at(탈퇴 시각) 하나로만 표현해 "정지(suspended)" 같은 중간 상태를
--   다룰 수 없었다. 프론트/관리자에서 바꾼 값이 DB에 반영되지 않는 갭을 메운다.
-- 설계: 모두 ADD COLUMN IF NOT EXISTS 로 멱등·비파괴 추가.
--   - signal_subscriptions.next_billing_at : 다음 정기결제 예정일(없으면 NULL).
--   - signal_subscriptions.auto_renew       : 자동갱신 여부(기본 FALSE — 명시적 갱신).
--   - users.status : active/suspended/deleted. 기존 deleted_at 과 함께 쓰되,
--     soft delete 시 status='deleted' 도 함께 세팅(조회 필터는 deleted_at 유지).
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

ALTER TABLE signal_subscriptions
    ADD COLUMN IF NOT EXISTS next_billing_at TIMESTAMPTZ;

ALTER TABLE signal_subscriptions
    ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active';

-- CHECK 제약은 멱등하게(이미 있으면 건너뜀) 추가.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_constraint WHERE conname = 'chk_users_status'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT chk_users_status
            CHECK (status IN ('active', 'suspended', 'deleted'));
    END IF;
END $$;
