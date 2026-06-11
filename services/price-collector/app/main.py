"""CLI entry point for the C-4 Price Collector batch.

Run on a Windows host with Kiwoom OpenAPI+ installed and a logged-in session:

    python -m app.main --all
    python -m app.main --tickers 005930 000660 --base-date 20260608
"""

import argparse
import sys

from app.core.config import get_settings
from app.core.constants import MVP_TICKERS
from app.kiwoom.client import PykiwoomClient, RateLimiter
from app.pipeline import PriceCollectionPipeline
from app.storage.repository import PostgresOhlcvRepository


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Signal Alpha price collector")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="collect the full MVP universe"
    )
    group.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="explicit list of 6-digit tickers"
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

    tickers = list(MVP_TICKERS) if args.all else args.tickers

    limiter = RateLimiter(
        min_interval_sec=settings.tr_delay_sec,
        max_per_minute=settings.tr_max_per_minute
    )
    client = PykiwoomClient(limiter=limiter)
    repository = PostgresOhlcvRepository(settings.database_url)
    pipeline = PriceCollectionPipeline(
        client=client,
        repository=repository,
        use_adjusted_price=settings.use_adjusted_price
    )

    batch = pipeline.run(tickers, args.base_date, args.run_mode)
    print(
        f"[{batch.status}] collected={batch.collected_count} "
        f"written={batch.inserted_count} failed={batch.failed_count}"
    )
    for result in batch.results:
        suffix = f" ({result.error})" if result.error else ""
        print(
            f"  {result.ticker}: {result.status} "
            f"candles={result.candle_count} written={result.written_count}{suffix}"
        )
    return 0 if batch.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
