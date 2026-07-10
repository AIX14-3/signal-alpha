"""저널 outcome 확정 러너 — 매일 1회(일봉 적재 후) 실행.

signal_journals(백엔드 DB)의 미확정 저널에 대해 ohlcv_data(수집 DB) 시계열로
7/30 거래일 outcome 을 계산해 signal_journal_outcomes(백엔드 DB)에 멱등 upsert 한다.
로직은 app/publish/journal_outcomes.py(연결 주입형) — 이 파일은 풀 배선만 한다.

가드: BACKEND_DATABASE_URL 미설정이면 no-op 종료(발행 publisher 와 동일 계약).
run_yesterday_flows.py(어제 일봉 적재) 이후에 돌려야 최신 종가가 반영된다.
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
logger = logging.getLogger("journal_outcomes")


async def main() -> None:
    load_env()
    backend_dsn = os.getenv("BACKEND_DATABASE_URL", "").strip()
    if not backend_dsn:
        logger.info("BACKEND_DATABASE_URL 미설정 — 저널 outcome 러너 no-op 종료")
        return

    from signal_alpha_data_access import DatabaseSettings, create_pool

    from app.publish.journal_outcomes import (
        record_journal_outcomes,
        sync_journal_chart_prices,
        sync_stock_logos,
        sync_stock_prices,
    )

    source_pool = await build_pool(max_pool_size=2)
    backend_pool = await create_pool(
        DatabaseSettings(database_url=backend_dsn, min_pool_size=1, max_pool_size=2)
    )
    try:
        async with backend_pool.acquire() as backend_conn, source_pool.acquire() as source_conn:
            stats = await record_journal_outcomes(backend_conn, source_conn)
            chart = await sync_journal_chart_prices(backend_conn, source_conn)
            prices = await sync_stock_prices(backend_conn, source_conn)
            logos = await sync_stock_logos(backend_conn, source_conn)
        print(
            f"[JOURNAL-OUTCOMES] stored={stats.stored} pending={stats.pending} "
            f"skipped={stats.skipped} failed={stats.failed}"
        )
        print(f"[JOURNAL-CHART] stocks={chart.stocks} rows={chart.rows} failed={chart.failed}")
        print(f"[STOCK-PRICES] stocks={prices.stocks} rows={prices.rows} failed={prices.failed}")
        print(f"[STOCK-LOGOS] stocks={logos.stocks} rows={logos.rows} failed={logos.failed}")
        if stats.errors:
            print("errors:", stats.errors[:10])
        if chart.errors:
            print("chart errors:", chart.errors[:10])
        if prices.errors:
            print("price errors:", prices.errors[:10])
        if logos.errors:
            print("logo errors:", logos.errors[:10])
    finally:
        await backend_pool.close()
        await source_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
