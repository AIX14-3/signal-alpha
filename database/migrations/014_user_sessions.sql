-- ============================================================================
-- User sessions for main-server refresh token management.
-- ============================================================================

CREATE TABLE user_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT,
    ip_address INET,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_sessions_user
    ON user_sessions (user_id, created_at DESC);

CREATE INDEX idx_user_sessions_expires_at
    ON user_sessions (expires_at);
