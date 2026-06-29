"""E2E 검증: 관리자/회원 hard delete 가 자식 행까지 실제로 정리하는지 확인한다.

회원 + 자식(구독·결제: 마이그레이션 전엔 NO ACTION 이라 삭제를 막던 테이블)을 만들고,
UserBillingRepository.hard_delete_user 로 'DELETE FROM users' 한 줄을 실행한 뒤
users 행과 자식 행이 모두 사라졌는지(FK ON DELETE CASCADE) 검증한다.

    uv run --package signal-alpha-main-server python services/main-server/scripts/verify_hard_delete.py
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import asyncpg
from signal_alpha_data_access.backend import UserBillingRepository

DEFAULT_DB = "postgresql://signal_alpha:pw@localhost:55432/signal_alpha"
_LETTERS, _DIGITS = "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789"


def _member_code() -> str:
    return "".join(secrets.choice(_LETTERS) for _ in range(4)) + "".join(
        secrets.choice(_DIGITS) for _ in range(4)
    )


async def main() -> None:
    db_url = os.getenv("DATABASE_URL", DEFAULT_DB)
    host = (urlparse(db_url).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"거부: 로컬 DB 전용입니다(host={host!r}).")

    conn = await asyncpg.connect(db_url)
    try:
        users = UserBillingRepository(conn)

        # 1) 회원 생성
        phone = "0109" + "".join(secrets.choice(_DIGITS) for _ in range(7))
        user = dict(
            await users.insert_identity_user(
                member_code=_member_code(),
                email=f"harddel-{secrets.token_hex(4)}@example.com",
                phone=phone,
                nickname="HardDelete Test",
            )
        )
        uid = int(user["id"])

        # 2) 자식 생성: 구독(signal_subscriptions) + 결제(payments)
        plan = await users.get_plan_by_type(os.getenv("SUBSCRIPTION_PLAN_TYPE", "monthly_9900"))
        if plan is None:
            raise SystemExit("구독 플랜 없음 — seeds 적용 필요.")
        sub = dict(
            await users.create_subscription(
                user_id=uid,
                plan_id=int(dict(plan)["id"]),
                status="active",
                expires_at=datetime.now(UTC) + timedelta(days=30),
                payment_method="verify-script",
                billing_cycle="monthly",
            )
        )
        pay_id = f"verify-{secrets.token_hex(6)}"
        await users.record_payment(
            user_id=uid,
            subscription_id=int(sub["id"]),
            imp_uid=pay_id,
            merchant_uid=pay_id,
            amount=9900,
            status="paid",
            paid_at=datetime.now(UTC),
            raw_response=json.dumps({"verify": True}),
        )

        async def counts() -> dict[str, int]:
            return {
                "users": await conn.fetchval("SELECT COUNT(*) FROM users WHERE id=$1", uid),
                "signal_subscriptions": await conn.fetchval(
                    "SELECT COUNT(*) FROM signal_subscriptions WHERE user_id=$1", uid
                ),
                "payments": await conn.fetchval(
                    "SELECT COUNT(*) FROM payments WHERE user_id=$1", uid
                ),
            }

        before = await counts()

        # 3) hard delete (DELETE FROM users) — FK CASCADE 가 자식을 정리해야 함
        status = await users.hard_delete_user(user_id=uid)
        after = await counts()

        ok = (
            before["users"] == 1
            and before["signal_subscriptions"] >= 1
            and before["payments"] >= 1
            and after == {"users": 0, "signal_subscriptions": 0, "payments": 0}
        )
        print(f"user_id={uid}  delete_status={status!r}")
        print(f"before={before}")
        print(f"after ={after}")
        print("RESULT:", "PASS (회원+자식 모두 삭제됨)" if ok else "FAIL")
        if not ok:
            raise SystemExit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
