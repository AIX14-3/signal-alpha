"""Backfill KR patents for target stocks from Google Patents (BigQuery).

KIPRIS Plus' free quota is monthly-capped (~1,000 calls), too small for bulk
historical collection of large-cap applicants. Historical patents therefore come
from the public ``patents-public-data.patents.publications`` dataset on BigQuery
instead (strategy: past=BigQuery bulk + latest=KIPRIS, since BigQuery lags ~18
months on the newest filings).

One BigQuery scan pulls every KR application in the year range whose harmonized
assignee matches a target company, attributes each row to a stock, and persists
through ``PatentCollector.ingest_records`` — the *same* DB contract as the live
KIPRIS collector (collector_runs + raw_documents + patent_raw_details +
processing_queue, F1 re-enqueue safety net). ``source_name='GOOGLE_PATENTS'``
marks the origin. The query/attribution logic lives in
``app/collectors/patent/bigquery_source.py`` so it can be reused by automation.

Cross-source dedup: ``source_hash`` is built from
``canonicalize_application_no(application_no)``, so the same patent later collected
via KIPRIS (13-digit form) collapses against the BigQuery row (``KR-...-A`` form)
on the ``source_hash`` UNIQUE constraint — re-running is idempotent within *and*
across sources.

Prerequisites:
  - gcloud ADC: ``gcloud auth application-default login`` (project patent-bq-reader).
  - ``google-cloud-bigquery`` — run via ``uv run --with google-cloud-bigquery ...``.
  - ``.env`` with ``DATABASE_URL`` (target DB) — only for a real (non --dry-run) load.

  # 1) inspect what would be loaded (no DB writes, no credentials needed beyond BQ)
  uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py --dry-run
  # 2) small smoke load to the DB
  uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py --limit-per-stock 50
  # 3) full load (3 companies, 2021-2023)
  uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Make ``app`` importable when run as ``python scripts/backfill_patents_bigquery.py``
# (Python puts scripts/ on sys.path, not the service root). parents[1] = agent-worker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.patent.bigquery_source import (  # noqa: E402  (after sys.path bootstrap)
    BQ_TABLE,
    DEFAULT_BQ_PROJECT,
    SOURCE_NAME,
    TICKER_BQ_PATTERNS,
    bq_rows,
    build_records,
)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill KR patents from Google Patents BigQuery")
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--tickers", default="005930,000660,035420", help="comma-separated")
    parser.add_argument("--limit-per-stock", type=int, default=None, help="cap records per stock (smoke test)")
    parser.add_argument("--batch-size", type=int, default=500, help="records per collector_run (progress/resume)")
    parser.add_argument("--dry-run", action="store_true", help="fetch + report only, no DB writes")
    parser.add_argument("--out", default=None, help="write per-stock record summary JSON here")
    parser.add_argument("--bq-project", default=None, help="BigQuery billing project (default patent-bq-reader)")
    args = parser.parse_args(argv)

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    unknown = [t for t in tickers if t not in TICKER_BQ_PATTERNS]
    if unknown:
        raise SystemExit(f"No BigQuery assignee patterns for tickers {unknown}; add them to TICKER_BQ_PATTERNS.")

    project = args.bq_project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_BQ_PROJECT
    all_patterns = [p for t in tickers for p in TICKER_BQ_PATTERNS[t]]

    print(f"[bq] querying {BQ_TABLE} (project={project}) filing {args.start_year}-{args.end_year}, tickers={tickers}")
    rows = bq_rows(
        start_year=args.start_year, end_year=args.end_year, patterns=all_patterns, project=project
    )
    print(f"[bq] {len(rows)} matching publication rows fetched")

    per_stock: dict[str, list] = {}
    for ticker in tickers:
        records = build_records(rows, ticker)
        if args.limit_per_stock is not None:
            records = records[: args.limit_per_stock]
        per_stock[ticker] = records
        sample = records[0] if records else None
        print(
            f"  {ticker}: {len(records)} unique applications"
            + (f"  e.g. {sample.application_no} {sample.invention_title!r}" if sample else "")
        )

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {t: [{"application_no": r.application_no, "title": r.invention_title,
                      "application_date": r.application_date, "ipc": r.ipc_code,
                      "applicant": r.applicant_name} for r in recs]
                 for t, recs in per_stock.items()},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[out] summary written to {args.out}")

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
    # KiprisClient is unused by ingest_records but PatentCollector requires a client.
    collector = PatentCollector(pool=pool, client=KiprisClient(api_key="unused-bigquery-backfill"))

    grand = {"inserted": 0, "skipped": 0, "requeued": 0, "failed": 0}
    try:
        async with pool.acquire() as conn:
            db_rows = await conn.fetch(
                "SELECT id, ticker, name FROM stocks WHERE ticker = ANY($1::text[])", tickers
            )
        stock_by_ticker = {r["ticker"]: {"id": r["id"], "name": r["name"]} for r in db_rows}
        missing = [t for t in tickers if t not in stock_by_ticker]
        if missing:
            raise SystemExit(f"Tickers not found in stocks table: {missing}")

        for ticker in tickers:
            stock = stock_by_ticker[ticker]
            records = per_stock[ticker]
            if not records:
                print(f"[{ticker}] {stock['name']}: 0 records, skipping")
                continue
            print(f"[{ticker}] {stock['name']} (stock_id={stock['id']}): loading {len(records)} records...")
            done = 0
            for batch in _chunks(records, args.batch_size):
                result = await collector.ingest_records(
                    stock_id=stock["id"], records=batch, source_name=SOURCE_NAME
                )
                grand["inserted"] += result["inserted_count"]
                grand["skipped"] += result["skipped_count"]
                grand["requeued"] += result["requeued_count"]
                grand["failed"] += result["failed_count"]
                done += len(batch)
                print(
                    f"  {done}/{len(records)}  +{result['inserted_count']} "
                    f"skip {result['skipped_count']} requeue {result['requeued_count']} "
                    f"fail {result['failed_count']} (run {result['collector_run_id']})"
                )
    finally:
        await pool.close()

    print(
        f"[done] inserted={grand['inserted']} skipped={grand['skipped']} "
        f"requeued={grand['requeued']} failed={grand['failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
