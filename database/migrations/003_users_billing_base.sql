CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    member_code VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT,
    nickname VARCHAR(50),
    agreed_risk BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS subscription_plans (
    id BIGSERIAL PRIMARY KEY,
    plan_type VARCHAR(20) NOT NULL UNIQUE,
    plan_display_name VARCHAR(50) NOT NULL,
    max_watchlist INTEGER NOT NULL DEFAULT 3,
    signal_delay_hours INTEGER NOT NULL DEFAULT 24,
    journal_max_entries INTEGER NOT NULL DEFAULT 50,
    has_alt_data BOOLEAN NOT NULL DEFAULT FALSE,
    has_detail_report BOOLEAN NOT NULL DEFAULT FALSE,
    has_backtesting BOOLEAN NOT NULL DEFAULT FALSE,
    price_monthly INTEGER NOT NULL DEFAULT 0,
    price_yearly INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
