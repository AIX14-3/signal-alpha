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

    async def insert_member(
        self,
        *,
        member_code: str,
        email: str,
        password_hash: str | None = None,
        nickname: str | None = None,
        phone: str | None = None,
        agreed_risk: bool = True,
        is_verified: bool = False,
    ) -> Any:
        """관리자 회원 생성 전용 평문 INSERT(충돌 시 UniqueViolation).

        create_user 의 ON CONFLICT(member_code) DO UPDATE 는 기존 계정을 덮어쓰므로
        신규 생성에는 쓰지 않는다. 호출부가 member_code 충돌 시 재시도한다.
        """
        return await self._connection.fetchrow(
            """
            INSERT INTO users (
                member_code, email, password_hash, nickname, phone,
                agreed_risk, is_verified
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            member_code,
            email,
            password_hash,
            nickname,
            phone,
            agreed_risk,
            is_verified,
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

    async def hard_delete_user(self, *, user_id: int) -> str:
        """회원 영구 삭제(hard delete). users 행을 DELETE 하면 FK ON DELETE 규칙으로
        소유 자식(portone_verifications/signal_journals/signal_subscriptions/
        terms_agreements/user_signal_reads/watchlists/payments/report_issuances/
        social_accounts/user_sessions)은 CASCADE 로 함께 삭제되고, analysis_requests 는
        SET NULL 로 분리된다(공용 시그널 보존).
        (마이그레이션 20260626_0244_user_owned_fks_on_delete_cascade_for_hard_delete.sql)

        반환: asyncpg 명령 상태 문자열(예: 'DELETE 1' / 'DELETE 0').
        """
        return await self._connection.execute(
            "DELETE FROM users WHERE id = $1",
            user_id,
        )

    async def set_user_status(self, *, user_id: int, status: str) -> Any:
        """회원 상태 변경(active/suspended). 탈퇴는 hard_delete_user(hard delete) 사용."""
        return await self._connection.fetchrow(
            """
            UPDATE users
            SET status = $2
            WHERE id = $1
              AND deleted_at IS NULL
            RETURNING *
            """,
            user_id,
            status,
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
        started_at: Any | None = None,
        expires_at: Any | None = None,
        next_billing_at: Any | None = None,
        auto_renew: bool = False,
        payment_method: str | None = None,
        billing_cycle: str | None = None,
    ) -> Any:
        """구독 생성. started_at 미지정 시 컬럼 기본값(NOW())을 사용한다."""
        return await self._connection.fetchrow(
            """
            INSERT INTO signal_subscriptions (
                user_id, plan_id, status, started_at, expires_at,
                next_billing_at, auto_renew, payment_method, billing_cycle
            )
            VALUES ($1, $2, $3, COALESCE($4, NOW()), $5, $6, $7, $8, $9)
            RETURNING *
            """,
            user_id,
            plan_id,
            status,
            started_at,
            expires_at,
            next_billing_at,
            auto_renew,
            payment_method,
            billing_cycle,
        )

    async def update_subscription_dates(
        self,
        *,
        user_id: int,
        expires_at: Any | None = None,
        next_billing_at: Any | None = None,
        auto_renew: bool | None = None,
    ) -> Any:
        """활성 구독의 만료일/다음 결제일/자동갱신을 부분 수정(None 은 기존 값 유지)."""
        return await self._connection.fetchrow(
            """
            UPDATE signal_subscriptions
            SET expires_at = COALESCE($2, expires_at),
                next_billing_at = COALESCE($3, next_billing_at),
                auto_renew = COALESCE($4, auto_renew),
                updated_at = NOW()
            WHERE user_id = $1
              AND status = 'active'
            RETURNING *
            """,
            user_id,
            expires_at,
            next_billing_at,
            auto_renew,
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

    async def resume_subscription(self, *, user_id: int) -> Any:
        """해지 예약(갱신 중지) 철회: cancelled_at 을 비워 정상 활성 상태로 되돌린다."""
        return await self._connection.fetchrow(
            """
            UPDATE signal_subscriptions
            SET cancelled_at = NULL, updated_at = NOW()
            WHERE user_id = $1
              AND status = 'active'
              AND cancelled_at IS NOT NULL
            RETURNING *
            """,
            user_id,
        )

    async def schedule_subscription_cancel(self, *, user_id: int) -> Any:
        """갱신 중지형 해지: status='active' 를 유지(만료일까지 접근권 유지)하고
        cancelled_at 으로 해지 의도만 기록한다. 만료 시 자연 종료."""
        return await self._connection.fetchrow(
            """
            UPDATE signal_subscriptions
            SET cancelled_at = NOW(), updated_at = NOW()
            WHERE user_id = $1
              AND status = 'active'
            RETURNING *
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

    async def get_payment_verification_by_imp_uid(self, *, imp_uid: str) -> Any:
        """결제 멱등 처리용: imp_uid(=paymentId)로 단건 조회. webhook↔confirm 중복 방지."""
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM portone_verifications
            WHERE imp_uid = $1
            """,
            imp_uid,
        )

    async def list_payment_verifications(self, *, user_id: int, limit: int = 50) -> list[Any]:
        """결제 내역(성공 건만) 최신순 조회."""
        return await self._connection.fetch(
            """
            SELECT *
            FROM portone_verifications
            WHERE user_id = $1
              AND verification_type = 'payment'
              AND status = 'paid'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
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

    async def record_payment(
        self,
        *,
        user_id: int,
        imp_uid: str,
        merchant_uid: str,
        amount: int,
        status: str = "paid",
        currency: str = "KRW",
        subscription_id: int | None = None,
        paid_at: Any | None = None,
        raw_response: Any | None = None,
    ) -> Any:
        """결제 성공 1건을 payments 이력에 append(덮어쓰지 않음)."""
        return await self._connection.fetchrow(
            """
            INSERT INTO payments (
                user_id, subscription_id, imp_uid, merchant_uid, amount,
                currency, status, paid_at, raw_response
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8, NOW()), $9)
            RETURNING *
            """,
            user_id,
            subscription_id,
            imp_uid,
            merchant_uid,
            amount,
            currency,
            status,
            paid_at,
            raw_response,
        )

    async def record_refund(
        self,
        *,
        user_id: int,
        imp_uid: str,
        merchant_uid: str,
        amount: int,
        refund_amount: int,
        cancel_reason: str | None = None,
        currency: str = "KRW",
        subscription_id: int | None = None,
        cancelled_at: Any | None = None,
        raw_response: Any | None = None,
    ) -> Any:
        """환불 1건을 payments 이력에 append. 전액=cancelled, 부분=partial_cancelled.

        원 결제행을 갱신하지 않고 별도 행을 추가해 '결제→환불' 이력을 보존한다.
        """
        status = "cancelled" if refund_amount >= amount else "partial_cancelled"
        return await self._connection.fetchrow(
            """
            INSERT INTO payments (
                user_id, subscription_id, imp_uid, merchant_uid, amount,
                currency, status, cancelled_at, refund_amount, cancel_reason, raw_response
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8, NOW()), $9, $10, $11)
            RETURNING *
            """,
            user_id,
            subscription_id,
            imp_uid,
            merchant_uid,
            amount,
            currency,
            status,
            cancelled_at,
            refund_amount,
            cancel_reason,
            raw_response,
        )

    async def list_payments(self, *, user_id: int, limit: int = 50) -> list[Any]:
        """결제/환불 전체 이력(최신순). 환불 행도 포함한다."""
        return await self._connection.fetch(
            """
            SELECT *
            FROM payments
            WHERE user_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )

    async def get_latest_paid_payment(self, *, user_id: int) -> Any:
        """환불 기준이 되는 가장 최근 '성공(paid)' 결제 1건."""
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM payments
            WHERE user_id = $1
              AND status = 'paid'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            user_id,
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
