-- 020_report_issuances.sql
-- 신규 기획: 리포트 "열람(언락)" 쿼터.
-- 리포트는 백엔드가 사전 생성·저장(final_signals, is_current/run_key 버전 관리)한다.
-- 사용자가 어떤 종목의 "현재 버전" 리포트를 처음 전체 열람(언락)하면 1건을 기록한다.
--   - issued_via='free'        : 비구독 회원이 무료 3회에서 차감하며 열람
--   - issued_via='subscription': 구독 활성 회원이 무제한 열람(무료 카운터 불변)
-- 멱등: (user_id, final_signal_id) UNIQUE → 동일 버전 재열람은 추가 차감 없음.
-- 실시간 변동으로 새 final_signals 버전이 생기면 새 final_signal_id 이므로 다시 1건(=1회 차감) 기록된다.
-- 무료 잔여 = GREATEST(0, 3 - COUNT(*) WHERE issued_via='free').
--   → 구독 우선 차감(구독 중 열람은 'subscription' 으로 기록되어 무료가 줄지 않음) +
--     무료 잔여분은 구독 만료 후에도 그대로 사용 가능, 두 규칙이 자연 충족된다.

CREATE TABLE IF NOT EXISTS report_issuances (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    final_signal_id BIGINT NOT NULL REFERENCES final_signals(id),
    run_key VARCHAR(30) NOT NULL,
    issued_via VARCHAR(20) NOT NULL
        CHECK (issued_via IN ('free', 'subscription')),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_report_issuance UNIQUE (user_id, final_signal_id)
);

CREATE INDEX IF NOT EXISTS idx_report_issuances_user
    ON report_issuances (user_id, issued_at DESC);

-- 무료 잔여 계산(issued_via 별 집계)용
CREATE INDEX IF NOT EXISTS idx_report_issuances_user_via
    ON report_issuances (user_id, issued_via);

-- 종목별 발행 이력 조회용
CREATE INDEX IF NOT EXISTS idx_report_issuances_user_stock
    ON report_issuances (user_id, stock_id, issued_at DESC);
