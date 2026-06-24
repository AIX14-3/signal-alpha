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

    async def get_user_by_phone(self, phone: str) -> Any:
        """활성(미탈퇴) 사용자만 조회. 본인인증 로그인·재가입 dedup 용."""
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM users
            WHERE phone = $1
              AND deleted_at IS NULL
            """,
            phone,
        )

    async def insert_identity_user(
        self,
        *,
        member_code: str,
        email: str,
        phone: str,
        nickname: str | None = None,
        agreed_risk: bool = True,
    ) -> Any:
        """포트원 본인인증 가입 전용 INSERT(평문 INSERT — member_code 충돌 시 UniqueViolation 발생).

        create_user 의 ON CONFLICT(member_code) DO UPDATE 는 충돌 시 기존 계정을 덮어쓰므로
        신규 가입에는 쓰지 않는다. 호출부가 member_code 충돌 시 재시도한다.
        """
        return await self._connection.fetchrow(
            """
            INSERT INTO users (
                member_code, email, phone, nickname, agreed_risk, is_verified
            )
            VALUES ($1, $2, $3, $4, $5, TRUE)
            RETURNING *
            """,
            member_code,
            email,
            phone,
            nickname,
            agreed_risk,
        )

    async def update_user_nickname(self, *, user_id: int, nickname: str | None) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE users
            SET nickname = $2
            WHERE id = $1
              AND deleted_at IS NULL
            RETURNING *
            """,
            user_id,
            nickname,
        )

    async def update_user_profile(
        self, *, user_id: int, nickname: str | None, email: str | None
    ) -> Any:
        """nickname/email 부분 수정(None 은 기존 값 유지)."""
        return await self._connection.fetchrow(
            """
            UPDATE users
            SET nickname = COALESCE($2, nickname),
                email = COALESCE($3, email)
            WHERE id = $1
              AND deleted_at IS NULL
            RETURNING *
            """,
            user_id,
            nickname,
            email,
        )

    async def soft_delete_user(self, *, user_id: int) -> None:
        """회원탈퇴(soft delete). 활성 phone 유니크가 해제되어 동일 번호 재가입 가능."""
        await self._connection.execute(
            """
            UPDATE users
            SET deleted_at = NOW()
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

    async def list_subscription_plans(self, *, active_only: bool = True) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM subscription_plans
            WHERE ($1::BOOLEAN IS FALSE OR is_active = TRUE)
            ORDER BY price_monthly ASC, id ASC
            """,
            active_only,
        )

    async def get_plan_by_type(self, plan_type: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM subscription_plans
            WHERE plan_type = $1
            """,
            plan_type,
        )

    async def get_subscription_by_user(self, *, user_id: int) -> Any:
        """현재 활성 구독을 plan 전체 컬럼과 조인해 1건 반환(없으면 None → free 간주)."""
        return await self._connection.fetchrow(
            """
            SELECT
                signal_subscriptions.id,
                signal_subscriptions.user_id,
                signal_subscriptions.plan_id,
                signal_subscriptions.status,
                signal_subscriptions.started_at,
                signal_subscriptions.expires_at,
                signal_subscriptions.cancelled_at,
                signal_subscriptions.payment_method,
                signal_subscriptions.billing_cycle,
                subscription_plans.plan_type,
                subscription_plans.plan_display_name,
                subscription_plans.max_watchlist,
                subscription_plans.signal_delay_hours,
                subscription_plans.journal_max_entries,
                subscription_plans.has_alt_data,
                subscription_plans.has_detail_report,
                subscription_plans.has_backtesting,
                subscription_plans.price_monthly,
                subscription_plans.price_yearly
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

    async def cancel_subscription(self, *, user_id: int) -> Any:
        """활성 구독을 취소 처리(부분 유니크 인덱스 idx_subscription_active 해제)."""
        return await self._connection.fetchrow(
            """
            UPDATE signal_subscriptions
            SET
                status = 'cancelled',
                cancelled_at = NOW(),
                updated_at = NOW()
            WHERE user_id = $1
              AND status = 'active'
            RETURNING *
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

    async def get_social_account(self, *, provider: str, provider_user_id: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT social_accounts.*, users.email AS user_email
            FROM social_accounts
            INNER JOIN users ON users.id = social_accounts.user_id
            WHERE social_accounts.provider = $1
              AND social_accounts.provider_user_id = $2
              AND users.deleted_at IS NULL
            """,
            provider,
            provider_user_id,
        )

    async def list_social_accounts(self, *, user_id: int) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT provider, created_at
            FROM social_accounts
            WHERE user_id = $1
            ORDER BY provider
            """,
            user_id,
        )

    async def delete_social_account(self, *, user_id: int, provider: str) -> Any:
        """연동 해제(토큰/행 삭제). 삭제된 행을 반환(없으면 None)."""
        return await self._connection.fetchrow(
            """
            DELETE FROM social_accounts
            WHERE user_id = $1 AND provider = $2
            RETURNING *
            """,
            user_id,
            provider,
        )

    async def get_latest_payment_verification(self, *, user_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM portone_verifications
            WHERE user_id = $1 AND verification_type = 'payment'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            user_id,
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
