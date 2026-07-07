"""커뮤니티 인기 랭킹 배치 러너 — 시간당 1회(크론) 실행.

backend DB(community_*)만 접근한다: 게시글/댓글/반응/조회를 집계해 가중합 점수를
community_post_rankings 에 (post_id, window_kind) 로 멱등 upsert 한다. weekly=롤링 7일,
all=전체. 로직은 app/publish/community_rankings.py(연결 주입형) — 이 파일은 풀 배선만 한다.

가드: BACKEND_DATABASE_URL 미설정이면 no-op 종료(발행 publisher 와 동일 계약).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker
from mvp_runtime import bootstrap, load_env

bootstrap()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("community_rankings")

_KST = ZoneInfo("Asia/Seoul")


async def main() -> None:
    load_env()
    backend_dsn = os.getenv("BACKEND_DATABASE_URL", "").strip()
    if not backend_dsn:
        logger.info("BACKEND_DATABASE_URL 미설정 — 커뮤니티 랭킹 러너 no-op 종료")
        return

    from signal_alpha_data_access import DatabaseSettings, create_pool

    from app.publish.community_rankings import record_community_rankings

    backend_pool = await create_pool(
        DatabaseSettings(database_url=backend_dsn, min_pool_size=1, max_pool_size=2)
    )
    try:
        async with backend_pool.acquire() as conn:
            stats = await record_community_rankings(conn, now=datetime.now(_KST))
        print(
            f"[COMMUNITY-RANKINGS] windows={stats.windows} upserts={stats.upserts} "
            f"pruned={stats.pruned} failed={stats.failed}"
        )
        if stats.errors:
            print("errors:", stats.errors[:10])
    finally:
        await backend_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
