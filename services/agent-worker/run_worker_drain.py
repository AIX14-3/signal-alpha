"""단발 전체-체인 큐 드레인 CLI — 라이브 E2E / CI 검증용 (#11 워커 영역 완성).

상주 데몬(``QUEUE_DRAIN_DAEMON_ENABLED``)을 켜지 않고도, processing_queue 를 **끝단(발행
PUBLISH_SIGNALS)까지** 한 번 비워 워커 체인이 정상 작동하는지 확인한다. 데몬과 동일한
``drain_until_idle`` 을 재사용하므로(전 task_type 체인 순서), run_pipeline.py(MVP 5종·발행
제외)로는 못 보던 발행 단계까지 검증된다.

  # 큐를 idle 까지 한 번 드레인하고 상태별 건수 출력:
  uv run python run_worker_drain.py

  # 포그라운드 연속 드레인(로컬 개발 — 데몬 없이 계속 소비):
  uv run python run_worker_drain.py --watch --interval-seconds 5

종목/분석을 큐에 채우는 것은 스케줄러(run_scheduler_instance.py) 또는 enqueue_stock_pipeline
의 몫이다. 이 CLI 는 이미 적재된 큐를 소비만 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env

bootstrap()


async def _run(args: argparse.Namespace) -> None:
    load_env()
    # bootstrap() 후라야 app.* import 가능(워크스페이스 패키지 경로 적재).
    from app.orchestrator.queue.drain_daemon import drain_until_idle

    pool = await build_pool(max_pool_size=max(2, args.pool_size))
    try:
        cycle = 0
        while True:
            cycle += 1
            counts = await drain_until_idle(pool)
            total = sum(counts.values())
            if total:
                print(f"[cycle {cycle}] drained {total}: {counts}")
            else:
                print(f"[cycle {cycle}] idle (큐 비어 있음)")
            if not args.watch:
                # 단발 모드: 종착 상태 요약 + 비정상(failed/skipped) 있으면 비-0 종료코드.
                bad = counts.get("failed", 0) + counts.get("skipped", 0)
                if bad:
                    raise SystemExit(f"드레인 중 실패/스킵 {bad}건 — 로그 확인.")
                return
            await asyncio.sleep(args.interval_seconds)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="processing_queue 를 발행까지 단발/연속 드레인.")
    parser.add_argument("--watch", action="store_true", help="포그라운드 연속 드레인(데몬 대용).")
    parser.add_argument("--interval-seconds", type=float, default=5.0, help="--watch 주기(초).")
    parser.add_argument("--pool-size", type=int, default=8, help="DB 풀 최대 크기.")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
