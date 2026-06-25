"""Phase 1 — small patent LLM-enrichment + analyzer-signal validation.

Test-stage dry run before committing to a full historical enrichment:
  1. sample N already-loaded GOOGLE_PATENTS patents per stock (2021-2023),
  2. backfill their Korean abstract from BigQuery into extra_payload (the live
     load stored title only; the enricher reads the abstract from there),
  3. run the production PatentEnricher (Gemini 2.5 Flash-Lite) with pacing to
     respect the free-tier RPM, caching llm_features / llm_status,
  4. run the production PatentAnalyzer per stock (as_of/lookback set to the data
     window) and print the resulting signal + indicators.

Reuses the shipping enricher/analyzer/loader/repository — no bespoke scoring.
The analyzer reads patent_raw_details directly, so normalization is not required
for a signal. Re-running is cheap: already-enriched patents skip (llm_status).

  uv run --with google-cloud-bigquery python scripts/phase1_patent_enrich_validate.py
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Korean-safe console

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"


class _PacedClient:
    """Wrap GeminiJsonClient.generate_json with a fixed inter-call delay so a
    sequential batch stays under the free-tier ~15 RPM limit (bursts -> 429)."""

    def __init__(self, inner: Any, pace: float) -> None:
        self._inner = inner
        self._pace = pace
        self._first = True

    async def generate_json(self, prompt: str) -> Any:
        if not self._first:
            await asyncio.sleep(self._pace)
        self._first = False
        return await self._inner.generate_json(prompt)


async def _run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1 patent enrich + analyzer validation")
    ap.add_argument("--per-stock", type=int, default=100)
    ap.add_argument("--tickers", default="005930,000660,035420")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--as-of", default="2023-12-31")
    ap.add_argument("--lookback", type=int, default=1100)
    ap.add_argument("--pace", type=float, default=4.5)
    ap.add_argument("--bq-project", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ["DATABASE_URL"]
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is required.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    project = args.bq_project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_BQ_PROJECT
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    import asyncpg
    from app.analyzers.config import PatentRuleConfig
    from app.analyzers.patent.analyzer import PatentAnalyzer
    from app.analyzers.patent.indicators import compute_indicators
    from app.clients.gemini_client import GeminiJsonClient
    from app.enrichment.patent_features import PatentEnricher
    from app.evidence_loaders.patent_loader import PatentEvidenceLoader
    from signal_alpha_data_access.repositories import RawDetailRepository

    conn = await asyncpg.connect(database_url)

    # 1) sample patents per stock
    stock_by_ticker: dict[str, dict] = {}
    selected: list[dict] = []
    for ticker in tickers:
        srow = await conn.fetchrow("SELECT id, name FROM stocks WHERE ticker = $1", ticker)
        if not srow:
            print(f"  [skip] ticker {ticker} not in stocks")
            continue
        stock_by_ticker[ticker] = {"id": srow["id"], "name": srow["name"]}
        rows = await conn.fetch(
            """
            SELECT rd.id AS raw_document_id, rd.external_id AS application_no
            FROM raw_documents rd JOIN patent_raw_details pd ON pd.raw_document_id = rd.id
            WHERE rd.source_name = 'GOOGLE_PATENTS' AND rd.stock_id = $1
              AND pd.application_date BETWEEN $2 AND $3
            ORDER BY random() LIMIT $4
            """,
            srow["id"], date.fromisoformat(args.start), date.fromisoformat(args.end), args.per_stock,
        )
        for r in rows:
            selected.append({"raw_document_id": r["raw_document_id"],
                             "application_no": r["application_no"], "ticker": ticker})
        print(f"  {ticker} {srow['name']}: sampled {len(rows)}")
    if not selected:
        raise SystemExit("no patents sampled")

    # 2) fetch abstracts from BigQuery and merge into extra_payload
    from google.cloud import bigquery  # type: ignore
    client = bigquery.Client(project=project)
    app_ids = [s["application_no"] for s in selected]
    sql = f"""
    SELECT application_number,
      (SELECT a.text FROM UNNEST(abstract_localized) a
         ORDER BY CASE LOWER(a.language) WHEN 'ko' THEN 0 WHEN 'en' THEN 1 ELSE 2 END
         LIMIT 1) AS abstract
    FROM `{BQ_TABLE}`
    WHERE country_code = 'KR' AND application_number IN UNNEST(@ids)
    """
    job = client.query(sql, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", app_ids)]))
    abstracts: dict[str, str] = {}
    for r in job.result():
        if r["abstract"] and not abstracts.get(r["application_number"]):
            abstracts[r["application_number"]] = r["abstract"]
    updated = 0
    for s in selected:
        ab = abstracts.get(s["application_no"])
        if not ab:
            continue
        await conn.execute(
            "UPDATE patent_raw_details SET extra_payload = extra_payload || $2::jsonb "
            "WHERE raw_document_id = $1",
            s["raw_document_id"], json.dumps({"abstract": ab}, ensure_ascii=False),
        )
        updated += 1
    print(f"[bq] abstracts merged into extra_payload for {updated}/{len(selected)} patents")

    # 3) enrich (production PatentEnricher) with pacing
    repo = RawDetailRepository(conn)
    paced = _PacedClient(GeminiJsonClient(api_key=api_key, model=model), args.pace)
    enricher = PatentEnricher(repo, paced, batch_limit=len(selected) + 10,
                              raw_document_ids=[s["raw_document_id"] for s in selected])
    print(f"[enrich] running {len(selected)} patents through {model} (pace {args.pace}s)... ~{len(selected)*args.pace/60:.0f} min")
    stats = await enricher.run()
    print(f"[enrich] {stats}")

    # 4) analyze each stock with the production analyzer (window set to the data)
    cfg = dataclasses.replace(PatentRuleConfig.from_env(), lookback_days=args.lookback)
    loader = PatentEvidenceLoader(repo, lookback_days=args.lookback)
    analyzer = PatentAnalyzer(cfg)
    as_of = date.fromisoformat(args.as_of)
    report: list[dict] = []
    print("\n" + "=" * 64)
    print(f"ANALYZER SIGNALS (as_of={as_of}, lookback={args.lookback}d)")
    print("=" * 64)
    for ticker, stock in stock_by_ticker.items():
        evidence = await loader.load(stock_id=stock["id"], stock_code=ticker, as_of=as_of)
        result = await analyzer.analyze(ticker, evidence)
        rows = evidence[0].metadata.get("rows", [])
        ind = compute_indicators(rows, as_of=as_of, lookback_days=args.lookback)
        print(f"\n### {ticker} {stock['name']}")
        print(f"  direction = {result.direction}   score = {result.score:+.3f}   data_status={result.data_status}")
        print(f"  total filings in window = {ind.total}  (recent {ind.recent_count} / prior {ind.prior_count}, "
              f"momentum {ind.momentum_ratio})")
        print(f"  LLM-enriched = {ind.llm_enriched_count}  mean_significance = {ind.mean_significance}  "
              f"max = {ind.max_significance}")
        print(f"  distinct tech categories = {ind.distinct_tech_categories}  new-category = {ind.new_category_count}")
        print(f"  summary: {result.summary}")
        if result.evidence_items:
            print(f"  highlights: {[e.title for e in result.evidence_items]}")
        report.append({
            "ticker": ticker, "name": stock["name"], "direction": result.direction,
            "score": result.score, "data_status": result.data_status,
            "total": ind.total, "recent": ind.recent_count, "prior": ind.prior_count,
            "llm_enriched": ind.llm_enriched_count, "mean_significance": ind.mean_significance,
            "max_significance": ind.max_significance,
        })

    await conn.close()
    if args.out:
        Path(args.out).write_text(json.dumps({"enrich": stats, "signals": report},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
