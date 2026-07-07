"""매매 부검 PIT 관측신호 오버레이 갱신 러너 — 매일 1회(공시 수집 후).

수집 DB(dart_ownership_events) 읽기 + backend DB(user_trade_signal_overlays) 쓰기 이중풀
(signal_journal_outcomes 러너와 동일 계약). 로직은 app/publish/trade_signal_overlay.py.

가드: BACKEND_DATABASE_URL 미설정이면 no-op 종료. DATABASE_URL(수집)로 dart 를 읽는다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker
from mvp_runtime import bootstrap, build_pool, load_env

bootstrap()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trade_signal_overlay")


async def main() -> None:
    load_env()
    backend_dsn = os.getenv("BACKEND_DATABASE_URL", "").strip()
    if not backend_dsn:
        logger.info("BACKEND_DATABASE_URL 미설정 — 부검 오버레이 러너 no-op 종료")
        return

    from signal_alpha_data_access import DatabaseSettings, create_pool

    from app.publish.trade_signal_overlay import refresh_signal_overlays

    source_pool = await build_pool(max_pool_size=2)
    backend_pool = await create_pool(
        DatabaseSettings(database_url=backend_dsn, min_pool_size=1, max_pool_size=2)
    )
    try:
        async with backend_pool.acquire() as backend_conn, source_pool.acquire() as source_conn:
            stats = await refresh_signal_overlays(backend_conn, source_conn)
        print(
            f"[TRADE-OVERLAY] stocks={stats.stocks} signals={stats.signals} failed={stats.failed}"
        )
        if stats.errors:
            print("errors:", stats.errors[:10])
    finally:
        await backend_pool.close()
        await source_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
