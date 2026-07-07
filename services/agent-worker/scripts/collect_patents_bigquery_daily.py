"""Daily incremental patent collect from Google Patents (BigQuery) by **공개일**.

특허는 출원 후 ~18개월 뒤에 공개된다. "오늘 새로 시장에 노출된 특허"는
출원일이 아니라 **publication_date(공개일)** 가 최근인 행이다. 이 스크립트는 매일
최근 N일(기본 90일) 공개분만 좁게 조회(``bq_rows_recent_publications``)해 저비용으로
신규 공개 특허를 적재한다 — 과거 대량 이력은 별도 ``backfill_patents_bigquery.py``
(출원연도 범위)가 담당한다.

KIPRIS 일간 수집과 상보적이며, 같은 특허가 두 소스에 겹치면
``canonicalize_application_no`` → ``source_hash`` UNIQUE 로 합쳐진다(KIPRIS 우선
dedup 은 PatentCollector 참조). ``source_name='GOOGLE_PATENTS'``.

    # 미리보기(쓰기 없음, DB 자격증명 불필요 — BQ 인증만)
    uv run --with google-cloud-bigquery python scripts/collect_patents_bigquery_daily.py --dry-run
    # 실제 적재(최근 90일 공개분, 전 종목)
    uv run --with google-cloud-bigquery python scripts/collect_patents_bigquery_daily.py

전제: gcloud ADC(``gcloud auth application-default login``), 실적재 시 ``.env`` 의
``DATABASE_URL``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Make ``app`` importable when run as ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.patent.bigquery_source import (  # noqa: E402  (after sys.path bootstrap)
    BQ_TABLE,
    DEFAULT_BQ_PROJECT,
    SOURCE_NAME,
    TICKER_BQ_PATTERNS,
    bq_rows_recent_publications,
    build_records,
)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _yyyymmdd(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


async def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily incremental patent collect from Google Patents BigQuery (by publication date)"
    )
    parser.add_argument("--window-days", type=int, default=90, help="공개일 창(오늘 기준 과거 N일)")
    parser.add_argument("--tickers", default=None, help="comma-separated (default: 전 종목)")
    parser.add_argument("--batch-size", type=int, default=500, help="records per collector_run")
    parser.add_argument("--dry-run", action="store_true", help="fetch + report only, no DB writes")
    parser.add_argument("--bq-project", default=None, help="BigQuery billing project (default patent-bq-reader)")
    args = parser.parse_args(argv)

    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else list(TICKER_BQ_PATTERNS.keys())
    )
    unknown = [t for t in tickers if t not in TICKER_BQ_PATTERNS]
    if unknown:
        raise SystemExit(f"No BigQuery assignee patterns for tickers {unknown}.")

    today = date.today()
    start = _yyyymmdd(today - timedelta(days=args.window_days))
    end = _yyyymmdd(today)
    project = args.bq_project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_BQ_PROJECT
    all_patterns = [p for t in tickers for p in TICKER_BQ_PATTERNS[t]]

    print(f"[bq] {BQ_TABLE} (project={project}) publication {start}-{end}, {len(tickers)} tickers")
    rows = bq_rows_recent_publications(start=start, end=end, patterns=all_patterns, project=project)
    print(f"[bq] {len(rows)} recently-published rows fetched")

    per_stock: dict[str, list] = {}
    for ticker in tickers:
        records = build_records(rows, ticker)
        per_stock[ticker] = records
        if records:
            print(f"  {ticker}: {len(records)} unique applications")

    if args.dry_run:
        total = sum(len(v) for v in per_stock.values())
        print(f"[dry-run] would load {total} patents across {len(tickers)} stocks. No DB writes.")
        return 0

    # --- real load ---
    try:  # repo / service .env supplies DATABASE_URL
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for a real load (use --dry-run to skip).")

    from app.clients.kipris_client import KiprisClient
    from app.collectors.patent import PatentCollector
    from signal_alpha_data_access import DatabaseSettings, create_pool

    pool = await create_pool(DatabaseSettings(database_url=database_url))
    collector = PatentCollector(pool=pool, client=KiprisClient(api_key="unused-bigquery-daily"))

    grand = {"inserted": 0, "skipped": 0, "requeued": 0, "failed": 0}
    try:
        async with pool.acquire() as conn:
            db_rows = await conn.fetch(
                "SELECT id, ticker, name FROM stocks WHERE ticker = ANY($1::text[])", tickers
            )
        stock_by_ticker = {r["ticker"]: {"id": r["id"], "name": r["name"]} for r in db_rows}

        for ticker in tickers:
            stock = stock_by_ticker.get(ticker)
            records = per_stock[ticker]
            if not records:
                continue
            if stock is None:
                print(f"[{ticker}] not in stocks table, skipping {len(records)} records")
                continue
            for batch in _chunks(records, args.batch_size):
                result = await collector.ingest_records(
                    stock_id=stock["id"], records=batch, source_name=SOURCE_NAME
                )
                grand["inserted"] += result["inserted_count"]
                grand["skipped"] += result["skipped_count"]
                grand["requeued"] += result["requeued_count"]
                grand["failed"] += result["failed_count"]
    finally:
        await pool.close()

    print(
        f"[done] inserted={grand['inserted']} skipped={grand['skipped']} "
        f"requeued={grand['requeued']} failed={grand['failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
