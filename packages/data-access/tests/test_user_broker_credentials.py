"""브로커 자격증명 리포지토리 — 암호화 저장·메타조회·복호·삭제·상태 (fake 연결)."""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from signal_alpha_data_access import crypto
from signal_alpha_data_access.repositories.user_broker_credentials import (
    UserBrokerCredentialRepository,
)

_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def key_env(monkeypatch):
    monkeypatch.setenv("BROKER_CRED_KEY", _KEY)


class _FakeConn:
    def __init__(self, *, secret_row=None, exec_result="DELETE 1"):
        self.insert_args = None
        self.fetch_sql = None
        self.executed = []  # (sql, args)
        self._secret_row = secret_row
        self._exec_result = exec_result

    async def fetchrow(self, sql, *args):
        if "INSERT INTO user_broker_credentials" in sql:
            assert "ON CONFLICT (user_id, broker, account_ref) DO UPDATE" in sql
            assert "app_key_enc" not in sql.split("RETURNING", 1)[1]  # RETURNING 에 암호문 없음
            self.insert_args = args
            return {
                "id": 1,
                "broker": args[1],
                "account_ref": args[2],
                "is_mock": args[3],
                "status": "active",
                "last_synced_at": None,
                "last_error": None,
                "created_at": None,
                "updated_at": None,
            }
        if "app_key_enc" in sql:  # get_secret_for_sync
            assert "status <> 'revoked'" in sql
            return self._secret_row
        return None

    async def fetch(self, sql, *args):
        self.fetch_sql = sql
        return []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return self._exec_result


def test_upsert_stores_ciphertext_not_plaintext():
    conn = _FakeConn()
    row = asyncio.run(
        UserBrokerCredentialRepository(conn).upsert_credential(
            user_id=7,
            broker="kiwoom",
            account_ref="ACC1",
            is_mock=True,
            app_key="APPKEY-plain",
            app_secret="SECRET-plain",
        )
    )
    # 저장 인자 5·6번이 암호문(bytes)이고 평문을 포함하지 않으며 복호 가능.
    key_enc, secret_enc = conn.insert_args[4], conn.insert_args[5]
    assert isinstance(key_enc, bytes) and isinstance(secret_enc, bytes)
    assert b"APPKEY-plain" not in key_enc and b"SECRET-plain" not in secret_enc
    assert crypto.decrypt_secret(key_enc) == "APPKEY-plain"
    assert crypto.decrypt_secret(secret_enc) == "SECRET-plain"
    # 반환(응답 소스)엔 키가 없다.
    assert "app_key_enc" not in row and "app_secret_enc" not in row
    assert row["broker"] == "kiwoom" and row["is_mock"] is True


def test_list_credentials_omits_ciphertext_columns():
    conn = _FakeConn()
    asyncio.run(UserBrokerCredentialRepository(conn).list_credentials(user_id=7))
    assert "user_broker_credentials" in conn.fetch_sql
    assert "enc" not in conn.fetch_sql  # app_key_enc/app_secret_enc 미조회


def test_get_secret_for_sync_decrypts():
    secret_row = {
        "id": 1,
        "user_id": 7,
        "broker": "toss",
        "account_ref": "A",
        "is_mock": False,
        "app_key_enc": crypto.encrypt_secret("K"),
        "app_secret_enc": crypto.encrypt_secret("S"),
    }
    conn = _FakeConn(secret_row=secret_row)
    out = asyncio.run(UserBrokerCredentialRepository(conn).get_secret_for_sync(credential_id=1))
    assert out["app_key"] == "K" and out["app_secret"] == "S"
    assert out["broker"] == "toss"


def test_get_secret_for_sync_missing_returns_none():
    conn = _FakeConn(secret_row=None)
    out = asyncio.run(UserBrokerCredentialRepository(conn).get_secret_for_sync(credential_id=99))
    assert out is None


def test_delete_returns_true_on_hit_false_on_miss():
    hit = _FakeConn(exec_result="DELETE 1")
    miss = _FakeConn(exec_result="DELETE 0")
    assert asyncio.run(
        UserBrokerCredentialRepository(hit).delete_credential(user_id=7, credential_id=1)
    )
    assert not asyncio.run(
        UserBrokerCredentialRepository(miss).delete_credential(user_id=7, credential_id=1)
    )


def test_mark_status_and_synced_emit_updates():
    conn = _FakeConn()
    repo = UserBrokerCredentialRepository(conn)
    asyncio.run(repo.mark_status(credential_id=1, status="error", last_error="boom"))
    asyncio.run(repo.mark_synced(credential_id=1))
    sqls = " ".join(sql for sql, _ in conn.executed)
    assert "SET status = $2, last_error = $3" in sqls
    assert "last_synced_at = now()" in sqls
