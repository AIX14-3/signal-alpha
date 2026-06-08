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
