from __future__ import annotations

from typing import Any


class UserSessionRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_session(
        self,
        *,
        user_id: int,
        refresh_token_hash: str,
        expires_at: Any,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO user_sessions (
                user_id, refresh_token_hash, user_agent, ip_address, expires_at
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            user_id,
            refresh_token_hash,
            user_agent,
            ip_address,
            expires_at,
        )

    async def get_active_session_by_refresh_hash(self, refresh_token_hash: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                user_sessions.*,
                users.id AS user_id,
                users.email AS user_email,
                users.nickname AS user_nickname,
                users.agreed_risk AS user_agreed_risk,
                users.is_verified AS user_is_verified
            FROM user_sessions
            INNER JOIN users
                ON users.id = user_sessions.user_id
            WHERE user_sessions.refresh_token_hash = $1
              AND user_sessions.revoked_at IS NULL
              AND user_sessions.expires_at > NOW()
              AND users.deleted_at IS NULL
            """,
            refresh_token_hash,
        )

    async def rotate_session(
        self,
        *,
        old_refresh_token_hash: str,
        new_refresh_token_hash: str,
        expires_at: Any,
    ) -> Any:
        """기존 활성 세션의 refresh 토큰을 제자리 회전(created_at 유지 → 절대 만료 앵커 보존)."""
        return await self._connection.fetchrow(
            """
            UPDATE user_sessions
            SET refresh_token_hash = $2, expires_at = $3
            WHERE refresh_token_hash = $1
              AND revoked_at IS NULL
            RETURNING *
            """,
            old_refresh_token_hash,
            new_refresh_token_hash,
            expires_at,
        )

    async def revoke_session_by_refresh_hash(self, refresh_token_hash: str) -> None:
        await self._connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = NOW()
            WHERE refresh_token_hash = $1
              AND revoked_at IS NULL
            """,
            refresh_token_hash,
        )
