"""자격증명 암호화 유틸 — 라운드트립·키부재·오키·마스킹 (Fernet)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from signal_alpha_data_access import crypto
from signal_alpha_data_access.crypto import CredentialCryptoError

_KEY = Fernet.generate_key().decode()


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv("BROKER_CRED_KEY", _KEY)


def test_roundtrip_and_no_plaintext_in_ciphertext(key_env):
    token = crypto.encrypt_secret("app-secret-1234")
    assert isinstance(token, bytes)
    assert b"app-secret-1234" not in token  # 암호문에 평문이 노출되지 않는다
    assert crypto.decrypt_secret(token) == "app-secret-1234"


def test_decrypt_accepts_memoryview(key_env):
    # asyncpg bytea 는 memoryview 로 올 수 있다.
    token = crypto.encrypt_secret("xyz")
    assert crypto.decrypt_secret(memoryview(token)) == "xyz"


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("BROKER_CRED_KEY", raising=False)
    with pytest.raises(CredentialCryptoError):
        crypto.encrypt_secret("x")


def test_empty_plaintext_rejected(key_env):
    with pytest.raises(CredentialCryptoError):
        crypto.encrypt_secret("")


def test_wrong_key_fails_to_decrypt(monkeypatch):
    monkeypatch.setenv("BROKER_CRED_KEY", _KEY)
    token = crypto.encrypt_secret("secret")
    monkeypatch.setenv("BROKER_CRED_KEY", Fernet.generate_key().decode())
    with pytest.raises(CredentialCryptoError):
        crypto.decrypt_secret(token)


def test_bad_key_format_raises(monkeypatch):
    monkeypatch.setenv("BROKER_CRED_KEY", "not-a-fernet-key")
    with pytest.raises(CredentialCryptoError):
        crypto.encrypt_secret("x")


def test_mask_secret():
    assert crypto.mask_secret("abcdefgh") == "****efgh"
    assert crypto.mask_secret("abc") == "****"
