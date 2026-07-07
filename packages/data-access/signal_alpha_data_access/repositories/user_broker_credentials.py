"""유저 증권사 API 자격증명 리포지토리 (backend DB).

유저가 입력한 브로커 앱키/시크릿을 at-rest 암호화(Fernet)해 저장한다. 평문은 저장·반환·
로그 어디에도 남기지 않는다(NFR-1) — 목록 조회는 암호문 컬럼을 아예 SELECT 하지 않고,
복호는 워커 동기화 경로(`get_secret_for_sync`)에서만 일어난다.

asyncpg 스타일: 키워드 전용 인자, $n 위치 파라미터, 반환은 raw Record(호출부 dict 래핑).
"""

from __future__ import annotations

from typing import Any

from signal_alpha_data_access.crypto import decrypt_secret, encrypt_secret

# 목록/응답에 내보내는 메타 컬럼(암호문 제외). app_key_enc/app_secret_enc 는 절대 넣지 않는다.
_META_COLUMNS = (
    "id, broker, account_ref, is_mock, status, last_synced_at, last_error, created_at, updated_at"
)


class UserBrokerCredentialRepository:
    """브로커 자격증명 등록/조회/해제 + 동기화 상태 갱신. backend 연결 위에서 동작."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_credential(
        self,
        *,
        user_id: int,
        broker: str,
        account_ref: str,
        is_mock: bool,
        app_key: str,
        app_secret: str,
    ) -> Any:
        """평문 키를 즉시 암호화해 저장(같은 계좌 재등록 시 갱신). 메타만 반환(암호문 제외)."""
        key_enc = encrypt_secret(app_key)
        secret_enc = encrypt_secret(app_secret)
        return await self._connection.fetchrow(
            f"""
            INSERT INTO user_broker_credentials
                (user_id, broker, account_ref, is_mock, app_key_enc, app_secret_enc, status, last_error)
            VALUES ($1, $2, $3, $4, $5, $6, 'active', NULL)
            ON CONFLICT (user_id, broker, account_ref) DO UPDATE SET
                is_mock = EXCLUDED.is_mock,
                app_key_enc = EXCLUDED.app_key_enc,
                app_secret_enc = EXCLUDED.app_secret_enc,
                status = 'active',
                last_error = NULL,
                updated_at = now()
            RETURNING {_META_COLUMNS}
            """,
            user_id,
            broker,
            account_ref,
            is_mock,
            key_enc,
            secret_enc,
        )

    async def list_credentials(self, *, user_id: int) -> list[Any]:
        """유저의 연동 목록(메타만 — 암호문 미조회)."""
        return await self._connection.fetch(
            f"SELECT {_META_COLUMNS} FROM user_broker_credentials "
            "WHERE user_id = $1 ORDER BY broker, account_ref",
            user_id,
        )

    async def get_secret_for_sync(self, *, credential_id: int) -> dict[str, Any] | None:
        """워커 전용: 복호해 브로커 호출에 쓴다. 반환 평문은 즉시 소비·로그 금지. revoked 제외."""
        row = await self._connection.fetchrow(
            "SELECT id, user_id, broker, account_ref, is_mock, app_key_enc, app_secret_enc "
            "FROM user_broker_credentials WHERE id = $1 AND status <> 'revoked'",
            credential_id,
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "broker": row["broker"],
            "account_ref": row["account_ref"],
            "is_mock": row["is_mock"],
            "app_key": decrypt_secret(row["app_key_enc"]),
            "app_secret": decrypt_secret(row["app_secret_enc"]),
        }

    async def delete_credential(self, *, user_id: int, credential_id: int) -> bool:
        """유저 소유 확인 후 삭제. 삭제된 행이 있으면 True(없으면 404 판단용)."""
        result = await self._connection.execute(
            "DELETE FROM user_broker_credentials WHERE id = $1 AND user_id = $2",
            credential_id,
            user_id,
        )
        return isinstance(result, str) and result.rsplit(" ", 1)[-1] == "1"

    async def mark_status(
        self, *, credential_id: int, status: str, last_error: str | None = None
    ) -> None:
        """동기화 결과 상태 기록(error 등). 워커 경로."""
        await self._connection.execute(
            "UPDATE user_broker_credentials SET status = $2, last_error = $3 WHERE id = $1",
            credential_id,
            status,
            last_error,
        )

    async def mark_synced(self, *, credential_id: int) -> None:
        """동기화 성공 — last_synced_at 갱신 + active 복귀 + 동기화 요청 소거. 워커 경로."""
        await self._connection.execute(
            "UPDATE user_broker_credentials SET last_synced_at = now(), status = 'active', "
            "last_error = NULL, sync_requested_at = NULL WHERE id = $1",
            credential_id,
        )

    async def request_sync(self, *, user_id: int) -> int:
        """유저의 활성 자격증명에 동기화 요청 플래그. 워커 러너가 우선 처리한다. 대상 수 반환."""
        result = await self._connection.execute(
            "UPDATE user_broker_credentials SET sync_requested_at = now() "
            "WHERE user_id = $1 AND status = 'active'",
            user_id,
        )
        if isinstance(result, str):
            tail = result.rsplit(" ", 1)[-1]
            return int(tail) if tail.isdigit() else 0
        return 0

    async def list_sync_targets(self, *, stale_before: Any) -> list[Any]:
        """동기화 대상 — 요청됐거나(온디맨드) 최초/오래된(주기 증분) 활성 자격증명 id 목록.

        요청분(sync_requested_at NOT NULL)을 먼저 처리한다.
        """
        return await self._connection.fetch(
            "SELECT id, user_id, broker, account_ref FROM user_broker_credentials "
            "WHERE status = 'active' AND ("
            "  sync_requested_at IS NOT NULL OR last_synced_at IS NULL OR last_synced_at < $1"
            ") ORDER BY sync_requested_at DESC NULLS LAST, id",
            stale_before,
        )
