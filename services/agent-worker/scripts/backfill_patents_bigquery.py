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
marks the origin. ``application_no`` is stored in BigQuery's native form
(``KR-YYYYNNNNNNN-A``), which differs from KIPRIS' 13-digit format — so re-running
this backfill is idempotent, but the same patent later collected via KIPRIS will
NOT collapse against the BigQuery row (different application_no → different hash).

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Make ``app`` importable when run as ``python scripts/backfill_patents_bigquery.py``
# (Python puts scripts/ on sys.path, not the service root). parents[1] = agent-worker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"
SOURCE_NAME = "GOOGLE_PATENTS"

# Ticker -> BigQuery assignee_harmonized.name UPPER LIKE patterns. Mirrors
# DEFAULT_COMPANIES.bq_like in scripts/patent_source_audit.py. ASCII-only so the
# same patterns are safe in the BigQuery console too.
TICKER_BQ_PATTERNS: dict[str, list[str]] = {
    "005930": ["%SAMSUNG ELECTRONICS%"],  # 삼성전자 (Samsung Electronics only — not SDI/Display/SDS)
    "000660": ["%SK HYNIX%"],             # SK하이닉스
    "035420": ["%NAVER%"],                # NAVER
}


def _like_predicate(pattern: str) -> Callable[[str], bool]:
    """Translate a SQL ``LIKE`` pattern (our patterns use only ``%`` wildcards on
    the ends) into a Python predicate over an already-upper-cased string."""
    p = pattern.upper()
    core = p.strip("%")
    starts, ends = p.startswith("%"), p.endswith("%")
    if starts and ends:
        return lambda s: core in s
    if starts:
        return lambda s: s.endswith(core)
    if ends:
        return lambda s: s.startswith(core)
    return lambda s: s == core


def _fmt_yyyymmdd(value: Any) -> str | None:
    """BigQuery dates are INT64 ``YYYYMMDD``. Return an 8-char string with any
    ``00`` month/day clamped to ``01`` (some records carry a zero day), or None."""
    if not value:
        return None
    s = str(int(value))
    if len(s) != 8:
        return None
    y, m, d = s[:4], s[4:6], s[6:8]
    if m == "00":
        m = "01"
    if d == "00":
        d = "01"
    return f"{y}{m}{d}"


def _bq_rows(*, start_year: int, end_year: int, patterns: list[str], project: str) -> list[dict]:
    """Fetch one row per (matching) publication across all target patterns in a
    single scan. Attribution to a specific stock happens later in Python."""
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        raise SystemExit(
            "google-cloud-bigquery is not installed — run via "
            "`uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py ...`"
        )
    try:
        client = bigquery.Client(project=project)
    except Exception as exc:  # DefaultCredentialsError 등
        raise SystemExit(
            f"BigQuery client init failed ({exc}).\n"
            "Run `gcloud auth application-default login` (project patent-bq-reader) first."
        )

    sql = f"""
    SELECT
      application_number,
      filing_date,
      publication_number,
      publication_date,
      (SELECT t.text FROM UNNEST(title_localized) t
         ORDER BY CASE LOWER(t.language) WHEN 'ko' THEN 0 WHEN 'en' THEN 1 ELSE 2 END
         LIMIT 1) AS title,
      (SELECT i.code FROM UNNEST(ipc) i LIMIT 1) AS ipc_code,
      ARRAY(SELECT a.name FROM UNNEST(assignee_harmonized) a) AS assignees
    FROM `{BQ_TABLE}`
    WHERE country_code = 'KR'
      AND filing_date BETWEEN @start AND @end
      AND EXISTS (
        SELECT 1 FROM UNNEST(assignee_harmonized) a, UNNEST(@patterns) p
        WHERE UPPER(a.name) LIKE p
      )
    """.strip()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("patterns", "STRING", [p.upper() for p in patterns]),
            bigquery.ScalarQueryParameter("start", "INT64", start_year * 10000 + 101),
            bigquery.ScalarQueryParameter("end", "INT64", end_year * 10000 + 1231),
        ]
    )
    rows = client.query(sql, job_config=job_config).result()
    return [
        {
            "application_number": str(r["application_number"]),
            "filing_date": r["filing_date"],
            "publication_number": r["publication_number"],
            "publication_date": r["publication_date"],
            "title": r["title"],
            "ipc_code": r["ipc_code"],
            "assignees": list(r["assignees"] or []),
        }
        for r in rows
    ]


def _build_records(rows: list[dict], ticker: str):
    """Attribute rows to ``ticker`` (assignee matches its patterns), de-dup by
    application_no, and build KiprisPatentRecord objects for ingest_records."""
    from app.clients.kipris_client import KiprisPatentRecord

    preds = [_like_predicate(p) for p in TICKER_BQ_PATTERNS[ticker]]
    seen: set[str] = set()
    records: list[Any] = []
    for row in rows:
        assignees = row["assignees"]
        matched = next((a for a in assignees if any(pred(a.upper()) for pred in preds)), None)
        if matched is None:
            continue
        app_no = row["application_number"]
        if not app_no or app_no in seen:
            continue
        seen.add(app_no)
        records.append(
            KiprisPatentRecord(
                application_no=app_no,
                invention_title=(row["title"] or app_no),
                applicant_name=matched,
                application_date=_fmt_yyyymmdd(row["filing_date"]),
                ipc_code=row["ipc_code"],
                open_date=_fmt_yyyymmdd(row["publication_date"]),
                registration_number=None,
                abstract=None,
                raw={
                    "source": "google_patents_bigquery",
                    "bq_table": BQ_TABLE,
                    "publication_number": row["publication_number"],
                    "publication_date": _fmt_yyyymmdd(row["publication_date"]),
                    "filing_date": _fmt_yyyymmdd(row["filing_date"]),
                    "assignees": assignees,
                    "ipc_code": row["ipc_code"],
                    "title": row["title"],
                    "matched_ticker": ticker,
                },
            )
        )
    return records


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
    rows = _bq_rows(
        start_year=args.start_year, end_year=args.end_year, patterns=all_patterns, project=project
    )
    print(f"[bq] {len(rows)} matching publication rows fetched")

    per_stock: dict[str, list] = {}
    for ticker in tickers:
        records = _build_records(rows, ticker)
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
