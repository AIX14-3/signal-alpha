"""민감 문자열(증권사 앱키/시크릿)의 at-rest 대칭 암호화 — Fernet.

유저가 입력한 브로커 자격증명을 DB에 평문으로 두지 않기 위한 최소 유틸. 마스터키는
env ``BROKER_CRED_KEY``(Fernet.generate_key() 형식 = urlsafe base64 32바이트). 키가
없거나 형식이 틀리면 **명확히 실패**한다 — 평문 저장 폴백은 없다(NFR-1).

Fernet 토큰은 IV·타임스탬프·HMAC 을 자체 포함하므로 별도 nonce 컬럼이 필요 없다.
main-server(등록 시 encrypt) 와 agent-worker(동기화 시 decrypt) 가 같은 마스터키를 공유한다.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_ENV_KEY = "BROKER_CRED_KEY"


class CredentialCryptoError(RuntimeError):
    """마스터키 부재/형식오류 또는 복호 실패. 내부 상세는 응답·로그로 노출하지 않는다."""


def _fernet() -> Fernet:
    raw = os.getenv(_ENV_KEY, "").strip()
    if not raw:
        raise CredentialCryptoError(f"{_ENV_KEY} 미설정 — 자격증명 암호화 불가(평문 저장 금지)")
    try:
        return Fernet(raw.encode("utf-8"))
    except (ValueError, TypeError) as exc:  # 잘못된 키 형식
        raise CredentialCryptoError(f"{_ENV_KEY} 형식 오류(Fernet key 아님)") from exc


def encrypt_secret(plaintext: str) -> bytes:
    """평문 → 암호문(bytea 저장용). 빈 값은 거부."""
    if not plaintext:
        raise CredentialCryptoError("빈 값은 암호화 대상이 아니다")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes | memoryview) -> str:
    """암호문 → 평문(워커 동기화 경로 전용). 결과는 즉시 소비하고 로그 금지."""
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError("복호 실패(마스터키 불일치 또는 손상된 토큰)") from exc


def mask_secret(plaintext: str) -> str:
    """응답 표시용 마스킹 — 뒤 4자만 노출."""
    if len(plaintext) <= 4:
        return "****"
    return "****" + plaintext[-4:]
