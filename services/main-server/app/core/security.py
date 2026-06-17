from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations_text, salt, expected = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != PASSWORD_ALGORITHM:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations_text),
    ).hex()
    return hmac.compare_digest(digest, expected)


def create_access_token(
    *,
    user_id: int,
    email: str,
    secret_key: str,
    expires_delta: timedelta,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "iat": now,
        "exp": int((datetime.now(UTC) + expires_delta).timestamp()),
    }
    return _encode_jwt(payload, secret_key)


def decode_access_token(token: str, *, secret_key: str) -> dict[str, Any]:
    payload = _decode_jwt(token, secret_key)
    if payload.get("type") != "access":
        raise ValueError("invalid_token_type")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("token_expired")
    return payload


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(refresh_token: str, *, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"),
        refresh_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _encode_jwt(payload: dict[str, Any], secret_key: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _base64url_json(header),
            _base64url_json(payload),
        ]
    )
    signature = hmac.new(
        secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_base64url_encode(signature)}"


def _decode_jwt(token: str, secret_key: str) -> dict[str, Any]:
    try:
        header_text, payload_text, signature_text = token.split(".")
    except ValueError as exc:
        raise ValueError("invalid_token") from exc
    signing_input = f"{header_text}.{payload_text}"
    expected = hmac.new(
        secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(_base64url_encode(expected), signature_text):
        raise ValueError("invalid_signature")
    payload = json.loads(_base64url_decode(payload_text))
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    return payload


def _base64url_json(value: dict[str, Any]) -> str:
    return _base64url_encode(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")
