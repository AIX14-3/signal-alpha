"""CLI entry point for the sector index batch (#27).

Run on the same Windows host as the price collector:

    python -m app.sector_main
    python -m app.sector_main --base-date 20260608
"""

import argparse
import sys

from app.core.config import get_settings
from app.kiwoom.client import PykiwoomClient, RateLimiter
from app.sector_pipeline import SectorCollectionPipeline
from app.storage.sector_repository import PostgresSectorRepository


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Signal Alpha sector index collector"
    )
    parser.add_argument(
        "--base-date",
        help="reference date YYYYMMDD (default: today)"
    )
    parser.add_argument(
        "--run-mode",
        choices=("batch", "immediate", "manual"),
        default="batch"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    limiter = RateLimiter(
        min_interval_sec=settings.tr_delay_sec,
        max_per_minute=settings.tr_max_per_minute
    )
    client = PykiwoomClient(limiter=limiter)
    repository = PostgresSectorRepository(settings.database_url)
    pipeline = SectorCollectionPipeline(client=client, repository=repository)

    batch = pipeline.run(args.base_date, args.run_mode)
    print(
        f"[{batch.status}] collected={batch.collected_count} "
        f"written={batch.inserted_count} failed={batch.failed_count}"
    )
    for result in batch.results:
        suffix = f" ({result.error})" if result.error else ""
        print(
            f"  {result.market}/{result.kiwoom_code}: {result.status} "
            f"candles={result.candle_count} written={result.written_count}{suffix}"
        )
    return 0 if batch.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
