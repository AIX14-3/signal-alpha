"""수집기 인스턴스 엔트리포인트 — 워커 프로세스에서 분리된 별도 수집 유닛 (#11 7영역 분리).

워커(agent-worker FastAPI)는 분석·집계·발행·큐 드레인을 담당하고, 수집은 이 별도 프로세스가
담당한다. 여기서는 **실시간 가격 수집 데몬(Kiwoom REST)** 을 상주 구동한다 — 기존엔 워커
lifespan 에 내장돼 있었으나(price-collector-daemon), 인스턴스 분리를 위해 분리 기동한다.

배포(7영역):
  - 워커 인스턴스:    PRICE_COLLECTOR_ENABLED=false, QUEUE_DRAIN_DAEMON_ENABLED=true
  - 수집기 인스턴스:  uv run python run_collector_instance.py

주기적 수집(patent/datalab)은 ``run_collectors.py --loop`` 또는 스케줄러가 트리거한다.
DART/report 수집은 큐 태스크(COLLECT_DART/COLLECT_REPORT)라 스케줄러→워커 드레인 경로로 처리된다.
이 엔트리포인트는 큐를 드레인하지 않는다(드레인은 워커의 책임).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env

bootstrap()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("collector_instance")


async def main() -> None:
    load_env()
    # bootstrap() 후라야 app.* 를 import 할 수 있다(워크스페이스 패키지 경로 적재).
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required for the collector instance.")
    if not (settings.kiwoom_app_key and settings.kiwoom_app_secret):
        raise SystemExit(
            "KIWOOM_APP_KEY/KIWOOM_APP_SECRET are required for the price collector."
        )

    pool = await build_pool(max_pool_size=8)
    try:
        from app.collectors.price.runner import supervise_daemon

        logger.info("수집기 인스턴스 기동 — 실시간 가격 수집 데몬")
        await supervise_daemon(pool, settings)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
