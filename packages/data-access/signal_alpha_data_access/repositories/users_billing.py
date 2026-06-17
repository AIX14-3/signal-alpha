from __future__ import annotations

from typing import Any


class UserBillingRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_user(
        self,
        *,
        member_code: str,
        email: str,
        password_hash: str | None = None,
        nickname: str | None = None,
        agreed_risk: bool = False,
        is_verified: bool = False,
        email_verified_at: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO users (
                member_code, email, password_hash, nickname, agreed_risk,
                is_verified, email_verified_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (member_code)
            DO UPDATE SET
                email = EXCLUDED.email,
                password_hash = EXCLUDED.password_hash,
                nickname = EXCLUDED.nickname,
                agreed_risk = EXCLUDED.agreed_risk,
                is_verified = EXCLUDED.is_verified,
                email_verified_at = EXCLUDED.email_verified_at
            RETURNING *
            """,
            member_code,
            email,
            password_hash,
            nickname,
            agreed_risk,
            is_verified,
            email_verified_at,
        )

    async def get_user_by_email(self, email: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM users
            WHERE email = $1
              AND deleted_at IS NULL
            """,
            email,
        )

    async def get_user_by_id(self, user_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM users
            WHERE id = $1
              AND deleted_at IS NULL
            """,
            user_id,
        )

    async def upsert_subscription_plan(
        self,
        *,
        plan_type: str,
        plan_display_name: str,
        max_watchlist: int = 3,
        signal_delay_hours: int = 24,
        journal_max_entries: int = 50,
        has_alt_data: bool = False,
        has_detail_report: bool = False,
        has_backtesting: bool = False,
        price_monthly: int = 0,
        price_yearly: int = 0,
        is_active: bool = True,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO subscription_plans (
                plan_type, plan_display_name, max_watchlist, signal_delay_hours,
                journal_max_entries, has_alt_data, has_detail_report,
                has_backtesting, price_monthly, price_yearly, is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (plan_type)
            DO UPDATE SET
                plan_display_name = EXCLUDED.plan_display_name,
                max_watchlist = EXCLUDED.max_watchlist,
                signal_delay_hours = EXCLUDED.signal_delay_hours,
                journal_max_entries = EXCLUDED.journal_max_entries,
                has_alt_data = EXCLUDED.has_alt_data,
                has_detail_report = EXCLUDED.has_detail_report,
                has_backtesting = EXCLUDED.has_backtesting,
                price_monthly = EXCLUDED.price_monthly,
                price_yearly = EXCLUDED.price_yearly,
                is_active = EXCLUDED.is_active
            RETURNING *
            """,
            plan_type,
            plan_display_name,
            max_watchlist,
            signal_delay_hours,
            journal_max_entries,
            has_alt_data,
            has_detail_report,
            has_backtesting,
            price_monthly,
            price_yearly,
            is_active,
        )

    async def create_subscription(
        self,
        *,
        user_id: int,
        plan_id: int,
        status: str = "active",
        expires_at: Any | None = None,
        payment_method: str | None = None,
        billing_cycle: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO signal_subscriptions (
                user_id, plan_id, status, expires_at, payment_method, billing_cycle
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            user_id,
            plan_id,
            status,
            expires_at,
            payment_method,
            billing_cycle,
        )

    async def get_active_subscription(self, *, user_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT signal_subscriptions.*, subscription_plans.plan_type
            FROM signal_subscriptions
            INNER JOIN subscription_plans
                ON subscription_plans.id = signal_subscriptions.plan_id
            WHERE signal_subscriptions.user_id = $1
              AND signal_subscriptions.status = 'active'
            ORDER BY signal_subscriptions.started_at DESC
            LIMIT 1
            """,
            user_id,
        )

    async def upsert_social_account(
        self,
        *,
        user_id: int,
        provider: str,
        provider_user_id: str,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_expires_at: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO social_accounts (
                user_id, provider, provider_user_id, access_token,
                refresh_token, token_expires_at
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (provider, provider_user_id)
            DO UPDATE SET
                user_id = EXCLUDED.user_id,
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                token_expires_at = EXCLUDED.token_expires_at
            RETURNING *
            """,
            user_id,
            provider,
            provider_user_id,
            access_token,
            refresh_token,
            token_expires_at,
        )

    async def record_portone_verification(
        self,
        *,
        user_id: int,
        imp_uid: str,
        merchant_uid: str,
        status: str,
        verification_type: str | None = None,
        verified_at: Any | None = None,
        raw_response: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO portone_verifications (
                user_id, imp_uid, merchant_uid, verification_type,
                status, verified_at, raw_response
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (imp_uid)
            DO UPDATE SET
                merchant_uid = EXCLUDED.merchant_uid,
                verification_type = EXCLUDED.verification_type,
                status = EXCLUDED.status,
                verified_at = EXCLUDED.verified_at,
                raw_response = EXCLUDED.raw_response
            RETURNING *
            """,
            user_id,
            imp_uid,
            merchant_uid,
            verification_type,
            status,
            verified_at,
            raw_response,
        )

    async def record_terms_agreement(
        self,
        *,
        user_id: int,
        terms_type: str,
        version: str,
        agreed: bool = True,
        ip_address: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO terms_agreements (
                user_id, terms_type, version, agreed, ip_address
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, terms_type, version)
            DO UPDATE SET
                agreed = EXCLUDED.agreed,
                agreed_at = NOW(),
                ip_address = EXCLUDED.ip_address
            RETURNING *
            """,
            user_id,
            terms_type,
            version,
            agreed,
            ip_address,
        )
