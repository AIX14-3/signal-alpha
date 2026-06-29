"""지정 종목의 발행 산출물을 백엔드 DB로 직접 발행(큐/LLM 없이 PUBLISH 핸들러만 호출)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("publish_one")

DEFAULT_TICKERS = ["005930", "000660", "035420"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", action="append")
    args = parser.parse_args()
    load_env()
    from app.publish.publish_task import PublishSignalsTaskHandler

    tickers = args.ticker or DEFAULT_TICKERS
    pool = await build_pool(max_pool_size=4)
    try:
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=None)
            handler = PublishSignalsTaskHandler(conn)
            for t in targets:
                res = await handler({"stock_id": int(t["stock_id"]), "task_context": {}})
                logger.info("발행 %s → %s", t["ticker"], res)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
