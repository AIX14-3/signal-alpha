-- 20260626_1410_payments_history.sql
-- ============================================================================
-- payments — 결제/환불 이력(append-only)
-- ----------------------------------------------------------------------------
-- 배경: 결제는 그동안 portone_verifications 한 테이블에만 적재됐고,
--   record_portone_verification 이 ON CONFLICT(imp_uid) DO UPDATE 로 같은 결제건을
--   덮어썼다. 환불이 일어나면 원 결제행(status='paid')이 status='refunded' 로 갱신되어
--   "결제→환불"의 이력이 사라지고, 금액/취소시각/사유도 남지 않는다. 관리자 화면의
--   결제 취소·구독 결제일 같은 변경이 DB에 온전히 반영되지 않는 핵심 원인.
-- 설계: imp_uid(포트원 결제건)당 여러 행을 허용하는 append-only 이력 테이블.
--   결제 성공 1행(paid) → 부분/전액 환불 시 별도 행(cancelled/partial_cancelled)을
--   추가로 INSERT 한다. portone_verifications 는 본인인증/멱등키 용도로 유지하고,
--   금액·환불 회계는 이 테이블이 단일 진실원이 된다.
--   subscription_id 는 어떤 구독 주기에 대한 결제인지 연결(NULL 허용 — 단건 결제 등).
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    subscription_id BIGINT REFERENCES signal_subscriptions(id),
    imp_uid VARCHAR(100) NOT NULL,        -- 포트원 결제건 식별자(paymentId)
    merchant_uid VARCHAR(100) NOT NULL,   -- 가맹점 주문번호
    amount INTEGER NOT NULL,              -- 결제 금액(원). 환불행은 환불 금액의 음수가 아니라 원 결제액을 기록하고 refund_amount 로 환불액을 표기
    currency VARCHAR(8) NOT NULL DEFAULT 'KRW',
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('paid', 'cancelled', 'partial_cancelled', 'failed')),
    paid_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    refund_amount INTEGER NOT NULL DEFAULT 0,   -- 이 행에서 환불된 금액(원). 전액환불=amount, 부분환불<amount
    cancel_reason TEXT,
    raw_response JSONB,                   -- 포트원 원본 응답(감사/대사용)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 사용자별 최신순 결제 내역 조회.
CREATE INDEX IF NOT EXISTS idx_payments_user_created
    ON payments (user_id, created_at DESC);

-- imp_uid 로 한 결제건의 모든 이력(결제+환불) 추적.
CREATE INDEX IF NOT EXISTS idx_payments_imp_uid
    ON payments (imp_uid);

-- 구독 주기별 결제 연결 조회.
CREATE INDEX IF NOT EXISTS idx_payments_subscription
    ON payments (subscription_id)
    WHERE subscription_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 백엔드 롤 권한(2-DB/단일DB 공통). 롤이 아직 없으면(로컬/dev) 건너뛴다.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.payments TO signal_backend;
        GRANT USAGE, SELECT ON SEQUENCE payments_id_seq TO signal_backend;
    END IF;
END $$;
