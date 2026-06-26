"""L6 event_study_panel 적재 배치 — forward-return 라벨 생성.

signal_events 를 앵커로 event_date 다음 영업일(asof)부터의 forward/abnormal return 을 계산해
event_study_panel 에 멱등 upsert 한다. base 모델·메타러너 학습(라벨) + lift 채택 게이트의 입력.

look-ahead 0: 진입 = event_date 보다 엄격히 큰 첫 세션(당일 종가 금지). 미래 윈도 미경과 라벨은 NULL.

Run (from services/agent-worker, LOCAL DB only):
    $env:DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    uv run python scripts/build_event_study_panel.py                      # 전체 signal_events
    uv run python scripts/build_event_study_panel.py --source DATALAB      # 소스 한정
    uv run python scripts/build_event_study_panel.py --universe kospi20_seed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.backtest.event_study import EventStudyBuilder  # noqa: E402


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


async def _main(source: str | None, universe: str) -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL 미설정")
    _require_local(db_url)

    filter_sql = ""
    filter_args: tuple[object, ...] = ()
    if source:
        filter_sql = "source_type = $1"
        filter_args = (source,)

    conn = await asyncpg.connect(db_url)
    try:
        builder = EventStudyBuilder(universe_snapshot=universe)
        count = await builder.run(
            conn, event_filter_sql=filter_sql, event_filter_args=filter_args
        )
        print(f"event_study_panel upsert: {count}행 (universe={universe}, source={source or '전체'})")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="L6 event_study_panel forward-return 라벨 적재")
    parser.add_argument(
        "--source",
        default=None,
        choices=["DART", "REPORT", "HIRING", "PATENT", "DATALAB"],
        help="signal_events.source_type 한정(미지정 시 전체)",
    )
    parser.add_argument("--universe", default="kospi20_seed", help="유니버스 스냅샷 태그")
    args = parser.parse_args()
    asyncio.run(_main(args.source, args.universe))


if __name__ == "__main__":
    main()
