"""Backfill KR patent raw rows from the stale Supabase store into the active Neon
collection DB — preserving abstracts and LLM enrichment (Option A: direct row copy).

target: collection

WHY THIS SCRIPT (vs ``backfill_patents_bigquery.py``)
-----------------------------------------------------
``backfill_patents_bigquery.py`` re-collects patents from Google Patents BigQuery
and would *re-derive* rows without the abstracts / LLM features that already exist.
Supabase (``DATABASE_URL``) already holds ~155k enriched patent rows collected over
months (abstracts in ``patent_raw_details.extra_payload``, LLM output in
``llm_features`` / ``llm_status`` / ``tech_category``). The active pipeline now runs
against Neon (``NEON_COLLECTION_URL``), which only has ~5 patent rows. This script
copies the *existing* enriched Supabase rows into Neon verbatim, so the RAG evidence
loader (``app/evidence_loaders/patent_loader.py``, which reads ``patent_raw_details``
directly) sees the full history without re-enrichment cost.

WHAT IT DOES
------------
For each target ticker it streams the joined ``raw_documents`` (source_type='PATENT')
+ ``patent_raw_details`` rows from Supabase and inserts them into Neon:

  * ``ticker -> stock_id`` is **re-resolved against Neon ``stocks``** (the two DBs
    have different universes / id sequences — never reuse the Supabase stock_id).
  * ``raw_documents`` ids are **not** copied; Neon assigns fresh ids and those new
    ids become the ``patent_raw_details.raw_document_id`` FK.
  * Row payload is preserved: ``source_hash, external_id, title, published_at,
    source_name, source_url, extra_payload(abstract), llm_features, llm_status,
    tech_category, application_no, application_date, applicant_name, is_new_category``.
  * ``collector_run_id`` points at one synthetic backfill ``collector_runs`` row
    (PATENT / manual) created per invocation, for provenance.

IDEMPOTENT
----------
``raw_documents`` insert uses ``ON CONFLICT DO NOTHING`` (covers the ``source_hash``
UNIQUE *and* ``(source_type, external_id)`` UNIQUE), so re-running skips rows already
present (incl. the pre-existing ~5). ``patent_raw_details`` insert likewise
``ON CONFLICT DO NOTHING`` (``application_no`` UNIQUE). Counts are reported as
inserted / skipped.

NORMALIZE is intentionally NOT enqueued — the patent RAG consumer reads
``patent_raw_details`` directly, so no ``processing_queue`` rows are created.

USAGE
-----
    # DSNs come from the repo-root .env (worktrees don't carry it — pass --env-file):
    ENV=../signal-alpha/.env   # adjust to your checkout

    # 1) dry-run (read + count only, no writes)
    python scripts/backfill_patents_from_supabase.py --env-file "$ENV" --dry-run
    # 2) smoke (035420, 100 rows, real load)
    python scripts/backfill_patents_from_supabase.py --env-file "$ENV" --smoke
    # 3) full 14-stock load
    python scripts/backfill_patents_from_supabase.py --env-file "$ENV"

DSN resolution order: ``--source-dsn`` / ``--target-dsn`` CLI > env
(``DATABASE_URL`` / ``NEON_COLLECTION_URL``). ``&channel_binding=require`` is stripped
because asyncpg does not understand it.

34-STOCK EXPANSION (this run is 14 only)
----------------------------------------
Source Supabase has PATENT data for 34 stocks (~155k rows); only 14 exist in the Neon
``stocks`` universe today, so 20 stocks (~40k rows) are skipped this run. To expand
later WITHOUT changing this script:

  1. Add the 20 tickers below to Neon ``stocks`` (migration / seed — a separate,
     approved change; DO NOT let this script or the backfill touch ``stocks``).
  2. Re-run with the widened ticker set, e.g.::

        python scripts/backfill_patents_from_supabase.py --env-file "$ENV" \
            --tickers 005930,000660,005380,000270,051910,066570,012330,204320,\
096770,035420,035720,000100,042700,068270,\
034220,011070,006400,018880,011210,240810,011170,108320,009830,251270,\
036570,128940,000990,247540,185750,054450,299030,228850,036690,302440

     The run is idempotent, so the 14 already loaded are skipped and only the new 20
     are inserted.

  20 missing tickers (name lives in Supabase ``stocks``; approx count in parens):
    034220 LG디스플레이(7982)  011070 LG이노텍(4801)   006400 삼성SDI(3523)
    018880 한온시스템(1864)    011210 현대위아(814)     240810 원익IPS(798)
    011170 롯데케미칼(773)     108320 LX세미콘(682)     009830 한화솔루션(599)
    251270 넷마블(384)         036570 엔씨소프트(351)   128940 한미약품(319)
    000990 DB하이텍(193)       247540 에코프로비엠(149) 185750 종근당(117)
    054450 (77)                299030 하나머티리얼즈(76) 228850 (75)
    036690 (55)                302440 SK바이오사이언스(54)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Neon universe intersection (14 stocks with patent data present in Neon `stocks`).
DEFAULT_TICKERS = [
    "005930", "000660", "005380", "000270", "051910", "066570", "012330",
    "204320", "096770", "035420", "035720", "000100", "042700", "068270",
]

SOURCE_QUERY = """
    SELECT
        r.source_name, r.external_id, r.source_hash, r.title, r.source_url,
        r.published_at, r.collect_status, r.collect_error, r.collected_at,
        r.collector_ver,
        p.application_no, p.patent_title, p.applicant_name, p.application_date,
        p.tech_category, p.is_new_category, p.extra_payload, p.llm_features,
        p.llm_status
    FROM raw_documents r
    JOIN patent_raw_details p ON p.raw_document_id = r.id
    JOIN stocks s ON s.id = r.stock_id
    WHERE r.source_type = 'PATENT' AND s.ticker = $1
    ORDER BY r.id
"""

INSERT_RAW = """
    INSERT INTO raw_documents (
        stock_id, collector_run_id, source_type, source_name, external_id,
        source_hash, title, source_url, published_at, collect_status,
        collect_error, collected_at, collector_ver
    )
    SELECT $1::bigint, $2::bigint, 'PATENT',
        t.source_name, t.external_id, t.source_hash, t.title, t.source_url,
        t.published_at, t.collect_status, t.collect_error, t.collected_at,
        t.collector_ver
    FROM unnest(
        $3::text[], $4::text[], $5::text[], $6::text[], $7::text[],
        $8::timestamptz[], $9::text[], $10::text[], $11::timestamptz[], $12::text[]
    ) AS t(
        source_name, external_id, source_hash, title, source_url,
        published_at, collect_status, collect_error, collected_at, collector_ver
    )
    ON CONFLICT DO NOTHING
    RETURNING id, source_hash
"""

INSERT_DETAIL = """
    INSERT INTO patent_raw_details (
        raw_document_id, stock_id, application_no, patent_title, applicant_name,
        application_date, tech_category, is_new_category, extra_payload,
        llm_features, llm_status
    )
    SELECT t.rid, $1::bigint, t.application_no, t.patent_title, t.applicant_name,
        t.application_date, t.tech_category, t.is_new_category,
        t.extra_payload::jsonb, t.llm_features::jsonb, t.llm_status
    FROM unnest(
        $2::bigint[], $3::text[], $4::text[], $5::text[], $6::date[],
        $7::text[], $8::boolean[], $9::text[], $10::text[], $11::text[]
    ) AS t(
        rid, application_no, patent_title, applicant_name, application_date,
        tech_category, is_new_category, extra_payload, llm_features, llm_status
    )
    ON CONFLICT DO NOTHING
"""


def _sanitize(dsn: str) -> str:
    # asyncpg does not understand libpq's channel_binding parameter.
    return (
        dsn.replace("&channel_binding=require", "")
        .replace("channel_binding=require&", "")
        .replace("?channel_binding=require", "")
    )


def _resolve_dsns(args: argparse.Namespace) -> tuple[str, str]:
    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=False)
    else:
        try:
            from dotenv import find_dotenv, load_dotenv

            load_dotenv(find_dotenv(usecwd=True), override=False)
        except ImportError:
            pass

    source = args.source_dsn or os.environ.get("SUPABASE_SOURCE_URL") or os.environ.get("DATABASE_URL")
    target = args.target_dsn or os.environ.get("NEON_COLLECTION_URL")
    if not source:
        raise SystemExit("source DSN missing (set --source-dsn or DATABASE_URL / SUPABASE_SOURCE_URL)")
    if not target:
        raise SystemExit("target DSN missing (set --target-dsn or NEON_COLLECTION_URL)")
    return _sanitize(source), _sanitize(target)


async def _flush_batch(
    tgt_conn,
    *,
    stock_id: int,
    collector_run_id: int | None,
    batch: list,
) -> tuple[int, int, int]:
    """Insert one batch (raw_documents + patent_raw_details) atomically.

    Returns (raw_inserted, raw_skipped, detail_inserted).
    """
    src_name = [r["source_name"] for r in batch]
    ext_id = [r["external_id"] for r in batch]
    s_hash = [r["source_hash"] for r in batch]
    title = [r["title"] for r in batch]
    src_url = [r["source_url"] for r in batch]
    published = [r["published_at"] for r in batch]
    c_status = [r["collect_status"] for r in batch]
    c_error = [r["collect_error"] for r in batch]
    collected = [r["collected_at"] for r in batch]
    c_ver = [r["collector_ver"] for r in batch]

    async with tgt_conn.transaction():
        # $8 published_at, $9 collect_status, $10 collect_error, $11 collected_at, $12 collector_ver
        returned = await tgt_conn.fetch(
            INSERT_RAW,
            stock_id, collector_run_id,
            src_name, ext_id, s_hash, title, src_url,
            published, c_status, c_error, collected, c_ver,
        )
        id_by_hash = {row["source_hash"]: row["id"] for row in returned}
        raw_inserted = len(returned)
        raw_skipped = len(batch) - raw_inserted

        # Build detail arrays only for freshly inserted raw_documents.
        rid, appno, ptitle, applicant, appdate = [], [], [], [], []
        tcat, isnew, epayload, lfeat, lstatus = [], [], [], [], []
        for r in batch:
            new_id = id_by_hash.get(r["source_hash"])
            if new_id is None:
                continue
            rid.append(new_id)
            appno.append(r["application_no"])
            ptitle.append(r["patent_title"])
            applicant.append(r["applicant_name"])
            appdate.append(r["application_date"])
            tcat.append(r["tech_category"])
            isnew.append(r["is_new_category"])
            epayload.append(r["extra_payload"])   # jsonb read as text; cast ::jsonb in SQL
            lfeat.append(r["llm_features"])        # None or jsonb-text
            lstatus.append(r["llm_status"])

        detail_inserted = 0
        if rid:
            status = await tgt_conn.execute(
                INSERT_DETAIL,
                stock_id, rid, appno, ptitle, applicant, appdate,
                tcat, isnew, epayload, lfeat, lstatus,
            )
            # status like 'INSERT 0 N'
            try:
                detail_inserted = int(status.split()[-1])
            except (ValueError, IndexError):
                detail_inserted = len(rid)

    return raw_inserted, raw_skipped, detail_inserted


async def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy enriched patent rows from Supabase into the Neon collection DB."
    )
    parser.add_argument("--tickers", default=None, help="comma-separated (default: 14 Neon-universe stocks)")
    parser.add_argument("--limit-per-stock", type=int, default=None, help="cap rows per stock (smoke)")
    parser.add_argument("--batch-size", type=int, default=2000, help="rows per insert batch / commit")
    parser.add_argument("--smoke", action="store_true", help="shortcut: --tickers 035420 --limit-per-stock 100")
    parser.add_argument("--dry-run", action="store_true", help="read + count only, no DB writes")
    parser.add_argument("--env-file", default=None, help="path to a .env with the DSNs")
    parser.add_argument("--source-dsn", default=None, help="override source (Supabase) DSN")
    parser.add_argument("--target-dsn", default=None, help="override target (Neon) DSN")
    args = parser.parse_args(argv)

    if args.smoke:
        tickers_csv = args.tickers or "035420"
        limit = args.limit_per_stock if args.limit_per_stock is not None else 100
    else:
        tickers_csv = args.tickers or ",".join(DEFAULT_TICKERS)
        limit = args.limit_per_stock
    tickers = [t.strip() for t in tickers_csv.split(",") if t.strip()]

    source_dsn, target_dsn = _resolve_dsns(args)

    import asyncpg

    src = await asyncpg.connect(source_dsn)
    tgt = await asyncpg.connect(target_dsn)
    grand = {"raw_inserted": 0, "raw_skipped": 0, "detail_inserted": 0, "source_rows": 0}
    collector_run_id: int | None = None
    try:
        # --- resolve ticker -> Neon stock_id (never reuse Supabase stock_id) ---
        rows = await tgt.fetch(
            "SELECT id, ticker, name FROM stocks WHERE ticker = ANY($1::text[])", tickers
        )
        stock_by_ticker = {r["ticker"]: r["id"] for r in rows}
        missing = [t for t in tickers if t not in stock_by_ticker]
        if missing:
            raise SystemExit(
                f"tickers not in Neon stocks (add via migration/seed first, then re-run): {missing}"
            )
        print(f"[cfg] {len(tickers)} tickers resolved in Neon; batch={args.batch_size} "
              f"limit_per_stock={limit} dry_run={args.dry_run}")

        if not args.dry_run:
            run_row = await tgt.fetchrow(
                """
                INSERT INTO collector_runs (collector_type, run_mode, status)
                VALUES ('PATENT', 'manual', 'running')
                RETURNING id
                """
            )
            collector_run_id = run_row["id"]
            print(f"[run] synthetic collector_run_id={collector_run_id}")

        for ticker in tickers:
            stock_id = stock_by_ticker[ticker]
            query = SOURCE_QUERY + ("\n    LIMIT $2" if limit else "")
            q_args = (ticker, limit) if limit else (ticker,)

            t_raw_ins = t_raw_skip = t_detail = t_src = 0
            batch: list = []

            async with src.transaction():  # cursor requires a transaction
                cur = src.cursor(query, *q_args, prefetch=1000)
                async for rec in cur:
                    t_src += 1
                    if args.dry_run:
                        continue
                    batch.append(rec)
                    if len(batch) >= args.batch_size:
                        ri, rs, di = await _flush_batch(
                            tgt, stock_id=stock_id, collector_run_id=collector_run_id, batch=batch
                        )
                        t_raw_ins += ri; t_raw_skip += rs; t_detail += di
                        batch = []
                if batch and not args.dry_run:
                    ri, rs, di = await _flush_batch(
                        tgt, stock_id=stock_id, collector_run_id=collector_run_id, batch=batch
                    )
                    t_raw_ins += ri; t_raw_skip += rs; t_detail += di

            grand["source_rows"] += t_src
            grand["raw_inserted"] += t_raw_ins
            grand["raw_skipped"] += t_raw_skip
            grand["detail_inserted"] += t_detail
            print(
                f"  [{ticker}] stock_id={stock_id} source={t_src} "
                f"inserted={t_raw_ins} skipped={t_raw_skip} detail={t_detail}"
            )

        if collector_run_id is not None:
            await tgt.execute(
                """
                UPDATE collector_runs
                SET status='success', finished_at=now(),
                    collected_count=$2, inserted_count=$3, skipped_count=$4
                WHERE id=$1
                """,
                collector_run_id,
                grand["source_rows"], grand["raw_inserted"], grand["raw_skipped"],
            )
    except BaseException:
        if collector_run_id is not None:
            try:
                await tgt.execute(
                    "UPDATE collector_runs SET status='failed', finished_at=now() WHERE id=$1",
                    collector_run_id,
                )
            except Exception:  # noqa: BLE001 — best-effort status update
                pass
        raise
    finally:
        await src.close()
        await tgt.close()

    if args.dry_run:
        print(f"[dry-run] would consider {grand['source_rows']} source rows "
              f"across {len(tickers)} stocks. No DB writes.")
    else:
        print(
            f"[done] source_rows={grand['source_rows']} "
            f"raw_inserted={grand['raw_inserted']} raw_skipped={grand['raw_skipped']} "
            f"detail_inserted={grand['detail_inserted']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(sys.argv[1:])))
