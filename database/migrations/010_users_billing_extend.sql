-- 010_users_billing_extend.sql
-- Zone F (User/Billing 확장): 구독·워치리스트·저널·본인인증·약관.
-- signal_journals / user_signal_reads가 final_signals(009)를 참조하므로 분석 Zone 뒤에 위치.

CREATE TABLE signal_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    plan_id BIGINT NOT NULL REFERENCES subscription_plans(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'cancelled', 'trial')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    payment_method VARCHAR(50),
    billing_cycle VARCHAR(10)
        CHECK (
            billing_cycle IN ('monthly', 'yearly')
            OR billing_cycle IS NULL
        ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_subscription_active
    ON signal_subscriptions (user_id)
    WHERE status = 'active';

CREATE TABLE watchlists (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    notification_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_watchlist UNIQUE (user_id, stock_id)
);

CREATE INDEX idx_watchlists_user
    ON watchlists (user_id);

CREATE INDEX idx_watchlists_stock
    ON watchlists (stock_id);

CREATE TABLE signal_journals (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    final_signal_id BIGINT REFERENCES final_signals(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    user_view VARCHAR(20) NOT NULL
        CHECK (user_view IN ('watch', 'bullish', 'bearish', 'neutral')),
    user_memo TEXT,
    decision_type VARCHAR(20),
    decision_reason TEXT,
    signal_score_at_time NUMERIC(5,2),
    signal_value_at_time VARCHAR(10),
    price_at_time NUMERIC(12,2),
    source_agreement_at_time VARCHAR(10),
    outcome_price NUMERIC(12,2),
    outcome_change_pct NUMERIC(6,2),
    outcome_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signal_journals_user_created
    ON signal_journals (user_id, created_at DESC);

CREATE INDEX idx_signal_journals_stock_created
    ON signal_journals (stock_id, created_at DESC);

CREATE INDEX idx_signal_journals_final_signal
    ON signal_journals (final_signal_id)
    WHERE final_signal_id IS NOT NULL;

CREATE TABLE user_signal_reads (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    final_signal_id BIGINT NOT NULL REFERENCES final_signals(id),
    read_date DATE NOT NULL DEFAULT CURRENT_DATE,
    read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_read UNIQUE (user_id, final_signal_id)
);

CREATE INDEX idx_user_signal_reads_user_read_at
    ON user_signal_reads (user_id, read_at DESC);

CREATE INDEX idx_user_signal_reads_final_signal
    ON user_signal_reads (final_signal_id);

CREATE TABLE social_accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('google', 'kakao', 'naver')),
    provider_user_id VARCHAR(100) NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_social UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_social_accounts_user
    ON social_accounts (user_id);

CREATE TABLE portone_verifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    imp_uid VARCHAR(100) NOT NULL UNIQUE,
    merchant_uid VARCHAR(100) NOT NULL,
    verification_type VARCHAR(20) CHECK (verification_type IN ('identity', 'payment')),
    status VARCHAR(20) NOT NULL,
    verified_at TIMESTAMPTZ,
    raw_response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_portone_verifications_user_created
    ON portone_verifications (user_id, created_at DESC);

CREATE INDEX idx_portone_verifications_merchant_uid
    ON portone_verifications (merchant_uid);

CREATE TABLE terms_agreements (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    terms_type VARCHAR(50) NOT NULL,
    version VARCHAR(20) NOT NULL,
    agreed BOOLEAN NOT NULL DEFAULT TRUE,
    agreed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address INET,
    CONSTRAINT uq_terms_agreement UNIQUE (user_id, terms_type, version)
);

CREATE INDEX idx_terms_agreements_user_agreed_at
    ON terms_agreements (user_id, agreed_at DESC);
