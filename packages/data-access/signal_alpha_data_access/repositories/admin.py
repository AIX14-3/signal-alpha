from __future__ import annotations

from typing import Any


class AdminRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_admin_account(
        self,
        *,
        email: str,
        password_hash: str,
        is_active: bool = True,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO admin_accounts (email, password_hash, is_active)
            VALUES ($1, $2, $3)
            ON CONFLICT (email)
            DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                is_active = EXCLUDED.is_active
            RETURNING *
            """,
            email,
            password_hash,
            is_active,
        )

    async def get_admin_by_email(self, email: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM admin_accounts
            WHERE email = $1
              AND is_active = TRUE
            """,
            email,
        )

    async def create_session(
        self,
        *,
        admin_id: int,
        session_token: str,
        expires_at: Any,
        last_activity_at: Any | None = None,
        ip_address: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO admin_sessions (
                admin_id, session_token, expires_at, last_activity_at, ip_address
            )
            VALUES ($1, $2, $3, COALESCE($4, NOW()), $5)
            ON CONFLICT (session_token)
            DO UPDATE SET
                expires_at = EXCLUDED.expires_at,
                last_activity_at = EXCLUDED.last_activity_at,
                ip_address = EXCLUDED.ip_address
            RETURNING *
            """,
            admin_id,
            session_token,
            expires_at,
            last_activity_at,
            ip_address,
        )

    async def delete_session(self, *, session_token: str) -> None:
        await self._connection.execute(
            """
            DELETE FROM admin_sessions
            WHERE session_token = $1
            """,
            session_token,
        )

    async def get_admin_by_id(self, admin_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM admin_accounts
            WHERE id = $1
              AND is_active = TRUE
            """,
            admin_id,
        )

    async def get_active_session(self, *, session_token: str) -> Any:
        """만료 전·활성 관리자 세션을 admin 계정과 조인해 반환(없으면 None)."""
        return await self._connection.fetchrow(
            """
            SELECT
                admin_sessions.id AS session_id,
                admin_sessions.session_token,
                admin_sessions.expires_at,
                admin_accounts.id AS admin_id,
                admin_accounts.email AS admin_email
            FROM admin_sessions
            INNER JOIN admin_accounts
                ON admin_accounts.id = admin_sessions.admin_id
            WHERE admin_sessions.session_token = $1
              AND admin_sessions.expires_at > NOW()
              AND admin_accounts.is_active = TRUE
            """,
            session_token,
        )

    async def update_last_login(self, *, admin_id: int) -> None:
        await self._connection.execute(
            """
            UPDATE admin_accounts
            SET last_login_at = NOW()
            WHERE id = $1
            """,
            admin_id,
        )

    async def list_users_paginated(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                users.id,
                users.member_code,
                users.email,
                users.nickname,
                users.created_at,
                sub.plan_type,
                sub.status AS subscription_status
            FROM users
            LEFT JOIN LATERAL (
                SELECT subscription_plans.plan_type, signal_subscriptions.status
                FROM signal_subscriptions
                INNER JOIN subscription_plans
                    ON subscription_plans.id = signal_subscriptions.plan_id
                WHERE signal_subscriptions.user_id = users.id
                  AND signal_subscriptions.status = 'active'
                ORDER BY signal_subscriptions.started_at DESC
                LIMIT 1
            ) sub ON TRUE
            WHERE users.deleted_at IS NULL
              AND (
                $3::VARCHAR IS NULL
                OR users.email ILIKE '%' || $3 || '%'
                OR users.nickname ILIKE '%' || $3 || '%'
              )
            ORDER BY users.created_at DESC, users.id DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
            query,
        )

    async def count_users(self, *, query: str | None = None) -> int:
        return await self._connection.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE deleted_at IS NULL
              AND (
                $1::VARCHAR IS NULL
                OR email ILIKE '%' || $1 || '%'
                OR nickname ILIKE '%' || $1 || '%'
              )
            """,
            query,
        )

    async def get_user_details(self, *, user_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                users.id,
                users.member_code,
                users.email,
                users.nickname,
                users.agreed_risk,
                users.is_verified,
                users.created_at,
                sub.plan_type,
                sub.status AS subscription_status,
                sub.started_at AS subscription_started_at,
                sub.expires_at AS subscription_expires_at,
                (
                    SELECT COUNT(*) FROM watchlists
                    WHERE watchlists.user_id = users.id
                ) AS watchlist_count
            FROM users
            LEFT JOIN LATERAL (
                SELECT
                    subscription_plans.plan_type,
                    signal_subscriptions.status,
                    signal_subscriptions.started_at,
                    signal_subscriptions.expires_at
                FROM signal_subscriptions
                INNER JOIN subscription_plans
                    ON subscription_plans.id = signal_subscriptions.plan_id
                WHERE signal_subscriptions.user_id = users.id
                  AND signal_subscriptions.status = 'active'
                ORDER BY signal_subscriptions.started_at DESC
                LIMIT 1
            ) sub ON TRUE
            WHERE users.id = $1
              AND users.deleted_at IS NULL
            """,
            user_id,
        )

    async def subscription_stats(self) -> Any:
        """전체 매출(MRR)·회원·활성 구독 집계 1행."""
        return await self._connection.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM users WHERE deleted_at IS NULL) AS total_users,
                (
                    SELECT COUNT(*) FROM signal_subscriptions
                    WHERE status = 'active'
                ) AS active_subscriptions,
                COALESCE((
                    SELECT SUM(subscription_plans.price_monthly)
                    FROM signal_subscriptions
                    INNER JOIN subscription_plans
                        ON subscription_plans.id = signal_subscriptions.plan_id
                    WHERE signal_subscriptions.status = 'active'
                ), 0) AS mrr
            """
        )

    async def subscription_counts_by_plan(self) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                subscription_plans.plan_type,
                COUNT(signal_subscriptions.id) AS count
            FROM subscription_plans
            LEFT JOIN signal_subscriptions
                ON signal_subscriptions.plan_id = subscription_plans.id
               AND signal_subscriptions.status = 'active'
            GROUP BY subscription_plans.plan_type, subscription_plans.price_monthly
            ORDER BY subscription_plans.price_monthly ASC
            """
        )
