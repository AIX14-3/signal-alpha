"""스케줄러 인스턴스 — 워커 HTTP 엔드포인트를 주기 호출 (#11 7영역 분리, 팀 스케줄러 경계 준수).

팀 스케줄러 계약(`docs/runbooks/agent-pipeline-schedule.md`): **스케줄러는 수집/분석 로직을 직접
들지 않고 워커의 내부 엔드포인트만 호출**한다. 그래서 이 프로세스는 DB 에 직접 인큐하지 않고
`POST /internal/schedules/{dart,report}/collect` 만 주기적으로 호출한다. 인큐된 COLLECT_* 는
워커 **큐 드레인 데몬**(`QUEUE_DRAIN_DAEMON_ENABLED`)이 정규화→분석→집계→종합→발행까지 소비한다.

운영에선 팀의 `ops/*.ps1`(Windows Task Scheduler)·cron·Cloud Scheduler 등도 같은 엔드포인트를
호출하면 된다 — 이 스크립트는 그중 "내부 워커 URL 에 도달 가능한 외부 스케줄러" 한 형태다.

  uv run python run_scheduler_instance.py                        # 1시간 주기 루프
  uv run python run_scheduler_instance.py --once                 # 1회만
  uv run python run_scheduler_instance.py --base-url http://localhost:8011 --interval-seconds 1800
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import load_env

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler_instance")

DEFAULT_BASE_URL = "http://localhost:8011"


async def run_cycle(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    dart_limit: int,
    report_limit: int,
    report_days_back: int,
) -> dict[str, object]:
    """스케줄러 1주기: 워커의 DART/Report 수집 스케줄 엔드포인트를 호출. 결과 요약 반환."""
    summary: dict[str, object] = {}

    async def _post(path: str, payload: dict[str, object]) -> object:
        resp = await client.post(f"{base_url.rstrip('/')}{path}", json=payload, timeout=60.0)
        resp.raise_for_status()
        return resp.json()

    # 1) DART 수집 스케줄 — 활성 종목의 COLLECT_DART 인큐(워커가 종목/윈도우 결정).
    try:
        dart = await _post("/internal/schedules/dart/collect", {"limit": dart_limit, "priority": "batch"})
        summary["dart"] = dart.get("scheduled_count") if isinstance(dart, dict) else dart
    except Exception as exc:  # noqa: BLE001 - 한 엔드포인트 실패가 다음 호출/다음 주기를 막지 않게
        logger.warning("dart/collect 실패: %s", exc)
        summary["dart"] = f"error: {exc}"

    # 2) Report 수집 스케줄 — 활성 종목의 COLLECT_REPORT 인큐.
    try:
        report = await _post(
            "/internal/schedules/report/collect",
            {"limit": report_limit, "days_back": report_days_back, "priority": "batch"},
        )
        summary["report"] = report.get("scheduled_count") if isinstance(report, dict) else report
    except Exception as exc:  # noqa: BLE001
        logger.warning("report/collect 실패: %s", exc)
        summary["report"] = f"error: {exc}"

    logger.info("스케줄러 주기 완료: %s", summary)
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Signal alpha scheduler: 워커 수집 엔드포인트를 주기 호출(큐 적재는 워커 드레인이 소비)."
    )
    parser.add_argument("--once", action="store_true", help="1회만 실행(루프 없이).")
    parser.add_argument("--interval-seconds", type=int, default=3600, help="주기(초). 기본 3600.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("WORKER_BASE_URL", DEFAULT_BASE_URL),
        help=f"워커 내부 URL. 기본 env WORKER_BASE_URL 또는 {DEFAULT_BASE_URL}.",
    )
    parser.add_argument("--dart-limit", type=int, default=10, help="DART 수집 회차당 종목 상한.")
    parser.add_argument("--report-limit", type=int, default=100, help="Report 수집 종목 상한.")
    parser.add_argument("--report-days-back", type=int, default=7, help="Report 수집 조회 일수.")
    args = parser.parse_args()

    load_env()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await run_cycle(
                    client,
                    base_url=args.base_url,
                    dart_limit=args.dart_limit,
                    report_limit=args.report_limit,
                    report_days_back=args.report_days_back,
                )
            except Exception:  # noqa: BLE001 - 한 주기 실패가 스케줄러를 죽이지 않게
                logger.exception("스케줄러 주기 실패 — 다음 주기에 재시도")
            if args.once:
                break
            await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
