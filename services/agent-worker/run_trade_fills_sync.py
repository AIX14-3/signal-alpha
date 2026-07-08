"""유저 체결내역 동기화 배치 러너 — 잦은 주기(크론) 실행.

요청됐거나(온디맨드 sync_requested_at) 오래된(주기 증분) 활성 브로커 자격증명을 순회하며
계좌 체결을 조회해 user_trade_fills(백엔드 DB)에 멱등 적재한다. 로직은
app/publish/trade_fills.py(연결·클라이언트 주입형) — 이 파일은 풀 배선만 한다.

가드: BACKEND_DATABASE_URL 미설정이면 no-op 종료(발행 publisher 와 동일 계약).
자격증명 복호에 BROKER_CRED_KEY(Fernet 마스터키)가 필요하다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker
from mvp_runtime import bootstrap, load_env

bootstrap()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trade_fills_sync")

_KST = ZoneInfo("Asia/Seoul")

# 주기 증분 임계 — 이만큼 지난 활성 자격증명은 요청이 없어도 재동기화한다.
_STALE_HOURS = 6


async def main() -> None:
    load_env()
    backend_dsn = os.getenv("BACKEND_DATABASE_URL", "").strip()
    if not backend_dsn:
        logger.info("BACKEND_DATABASE_URL 미설정 — 체결 동기화 러너 no-op 종료")
        return

    from signal_alpha_data_access import DatabaseSettings, create_pool

    from app.publish.trade_fills import sync_due_credentials

    now = datetime.now(_KST)
    stale_before = now - timedelta(hours=_STALE_HOURS)
    backend_pool = await create_pool(
        DatabaseSettings(database_url=backend_dsn, min_pool_size=1, max_pool_size=2)
    )
    try:
        async with backend_pool.acquire() as conn:
            summary = await sync_due_credentials(conn, stale_before=stale_before, now=now)
        print(
            f"[TRADE-FILLS-SYNC] credentials={summary['credentials']} "
            f"inserted={summary['inserted']} failed={summary['failed_credentials']}"
        )
        if summary["errors"]:
            print("errors:", summary["errors"][:10])
    finally:
        await backend_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
