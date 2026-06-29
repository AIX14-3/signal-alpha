"""E2E QA 전용: 로컬 DEV DB에 '인증된 테스트 사용자'를 시드한다.

배경(왜 이 스크립트가 필요한가):
- 프론트/백엔드 모두 PortOne를 **항상 real 모드**로만 호출한다(dev 폴백을 의도적으로 제거).
  → 회원가입은 실 본인인증(SMS)을, 결제 confirm은 실 카드를 강제하므로, browser-harness 같은
    자동화로 로그인/구독 상태를 UI만으로 만들 수 없다.
- 그래서 **앱 코드를 바꾸지 않고** DB에 사용자 + 세션(refresh)을 직접 시드하고, browser-harness가
  `sa_refresh` 쿠키를 주입하면 프론트 부팅 hydrate(AppShell → refreshSession → /api/auth/refresh)가
  access 토큰을 발급해 '로그인된 상태'가 된다.
- 구독중(무제한) 상태가 필요하면 결제 우회로 구독행을 직접 시드한다(E2E_SUBSCRIBE=1).

사용법(레포 루트에서):
    # DEV DB가 떠 있어야 함(docker compose up -d postgres + 마이그레이션/seeds 적용)
    uv run --package signal-alpha-main-server python services/main-server/scripts/seed_e2e_user.py

출력(JSON)의 refresh_token 을 browser-harness 쿠키 주입에 사용한다(web/qa/README.md 참조).

환경변수:
    DATABASE_URL            기본 dev DB(localhost:55432). **localhost가 아니면 거부**(안전장치).
    AUTH_SECRET_KEY         refresh 해시 키. 백엔드 기본값과 반드시 동일해야 함
                            (기본 dev-main-server-secret-change-me).
    SUBSCRIPTION_PLAN_TYPE  구독 플랜 타입(기본 monthly_9900, seeds 002로 적재됨).
    E2E_EMAIL / E2E_PHONE / E2E_NICKNAME   시드 사용자 식별자(기본 고정값 → 재실행 시 재사용).
    E2E_SUBSCRIBE=1         활성 구독행도 시드(구독중·무제한 UI 검증용).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import asyncpg
from signal_alpha_data_access.backend import UserBillingRepository, UserSessionRepository

DEFAULT_DB = "postgresql://signal_alpha:pw@localhost:55432/signal_alpha"

_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_DIGITS = "23456789"


def _member_code() -> str:
    letters = "".join(secrets.choice(_CODE_LETTERS) for _ in range(4))
    digits = "".join(secrets.choice(_CODE_DIGITS) for _ in range(4))
    return f"{letters}{digits}"


def _hash_refresh(token: str, secret: str) -> str:
    # app.core.security.hash_refresh_token 과 동일한 HMAC-SHA256.
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _hash_password(password: str) -> str:
    # app.core.security.hash_password 와 동일한 pbkdf2_sha256(260k) 포맷.
    iterations = 260_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def _assert_local(db_url: str) -> None:
    host = (urlparse(db_url).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(
            f"거부: DATABASE_URL 호스트가 로컬이 아닙니다({host!r}). "
            "이 스크립트는 로컬 DEV DB 전용입니다."
        )
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise SystemExit("거부: APP_ENV=production 에서는 실행할 수 없습니다.")


async def _get_or_create_user(users: UserBillingRepository, *, email: str, phone: str, nickname: str) -> dict:
    existing = await users.get_user_by_phone(phone)
    if existing is not None:
        return dict(existing)
    for _ in range(6):
        try:
            row = await users.insert_identity_user(
                member_code=_member_code(),
                email=email,
                phone=phone,
                nickname=nickname,
                agreed_risk=True,
            )
            return dict(row)
        except asyncpg.UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", "") or ""
            if "phone" in constraint or "email" in constraint:
                raise SystemExit(
                    f"이미 다른 사용자가 같은 phone/email을 사용 중입니다: {exc}"
                ) from None
            continue  # member_code 충돌 → 재생성
    raise SystemExit("member_code 생성 재시도 초과")


async def main() -> None:
    db_url = os.getenv("DATABASE_URL", DEFAULT_DB)
    secret = os.getenv("AUTH_SECRET_KEY", "dev-main-server-secret-change-me")
    plan_type = os.getenv("SUBSCRIPTION_PLAN_TYPE", "monthly_9900")
    email = os.getenv("E2E_EMAIL", "e2e-tester@example.com")
    phone = "".join(ch for ch in os.getenv("E2E_PHONE", "01000000000") if ch.isdigit())
    nickname = os.getenv("E2E_NICKNAME", "E2E Tester")
    want_subscription = os.getenv("E2E_SUBSCRIBE", "0") == "1"

    _assert_local(db_url)

    connection = await asyncpg.connect(db_url)
    try:
        users = UserBillingRepository(connection)
        sessions = UserSessionRepository(connection)

        user = await _get_or_create_user(users, email=email, phone=phone, nickname=nickname)
        user_id = int(user["id"])

        # 알려진 refresh 원문 토큰 → 해시로 세션 INSERT. 원문은 쿠키 주입에 사용.
        refresh_token = "e2e-" + secrets.token_urlsafe(32)
        await sessions.create_session(
            user_id=user_id,
            refresh_token_hash=_hash_refresh(refresh_token, secret),
            expires_at=datetime.now(UTC) + timedelta(days=14),
            user_agent="browser-harness-e2e",
        )

        subscription = None
        if want_subscription:
            plan_row = await users.get_plan_by_type(plan_type)
            if plan_row is None:
                raise SystemExit(
                    f"구독 플랜 '{plan_type}' 이 없습니다. seeds(002_seed_subscription_plans.sql)를 적용하세요."
                )
            sub_row = await users.create_subscription(
                user_id=user_id,
                plan_id=int(dict(plan_row)["id"]),
                status="active",
                expires_at=datetime.now(UTC) + timedelta(days=30),
                payment_method="e2e-seed",
                billing_cycle="monthly",
            )
            subscription = "active(monthly, +30d)" if sub_row is not None else None

        admin = None
        if os.getenv("E2E_ADMIN", "0") == "1":
            admin_email = os.getenv("E2E_ADMIN_EMAIL", "e2e-admin@example.com").strip().lower()
            admin_password = os.getenv("E2E_ADMIN_PASSWORD", "admin1234!")
            row = await connection.fetchrow(
                """
                INSERT INTO admin_accounts (email, password_hash, is_active)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (email)
                DO UPDATE SET password_hash = EXCLUDED.password_hash, is_active = TRUE
                RETURNING id, email
                """,
                admin_email,
                _hash_password(admin_password),
            )
            admin = {"id": int(row["id"]), "email": row["email"], "password": admin_password}

        print(
            json.dumps(
                {
                    "user_id": user_id,
                    "admin": admin,
                    "email": user.get("email"),
                    "member_code": user.get("member_code"),
                    "refresh_token": refresh_token,
                    "subscription": subscription,
                    "cookie": {
                        "name": "sa_refresh",
                        "value": refresh_token,
                        "domain": "localhost",
                        "path": "/api/auth",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
