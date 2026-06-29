"""Bulk patent LLM enrichment from Google Patents abstracts (paid Gemini tier).

Scope-selectable enrichment for already-loaded GOOGLE_PATENTS patents:
  1. select pending patents (llm_status='pending') for the given tickers/year range,
  2. backfill their Korean abstract from BigQuery into extra_payload,
  3. call Gemini CONCURRENTLY (reusing the production prompt/validation from
     app.enrichment.patent_features) and cache llm_features / llm_status,
  4. optionally run the PatentAnalyzer per stock and print the resulting signal.

Concurrency (vs the sequential production PatentEnricher) is what makes ~10k
patents finish in minutes on the paid tier. Requires billing on the Gemini key:
the free tier caps gemini-2.5-flash-lite at 20 requests/DAY. Re-running is safe —
only llm_status='pending' rows are selected, so finished patents are skipped.

  uv run --with google-cloud-bigquery python scripts/enrich_patents_llm.py \
      --tickers 005930,000660,035420 --start 2023-01-01 --end 2023-12-31
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
PRICE_IN_PER_M, PRICE_OUT_PER_M = 0.10, 0.40
_RETRYABLE = {429, 500, 502, 503, 504}


def _call_gemini(prompt: str, *, model: str, api_key: str, timeout: float = 60.0) -> dict:
    url = ENDPOINT.format(model=model, key=api_key)
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    req = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "retryable": exc.code in _RETRYABLE}
    except (URLError, TimeoutError) as exc:
        return {"ok": False, "status": str(exc), "retryable": True}
    usage = payload.get("usageMetadata", {})
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "status": "no-candidate", "retryable": True}
    return {"ok": True, "text": text, "in_tok": usage.get("promptTokenCount", 0),
            "out_tok": usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)}


async def _call_with_retry(prompt, *, model, api_key, attempts=5) -> dict:
    last = None
    for i in range(attempts):
        res = await asyncio.to_thread(_call_gemini, prompt, model=model, api_key=api_key)
        if res.get("ok") or not res.get("retryable"):
            return res
        last = res
        await asyncio.sleep(min(2.0 ** i, 30.0))
    return last or {"ok": False, "status": "exhausted"}


async def _run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bulk patent LLM enrichment (paid Gemini tier)")
    ap.add_argument("--tickers", default="005930,000660,035420")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--source-name", default="GOOGLE_PATENTS")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="cap total patents (testing)")
    ap.add_argument("--as-of", default="2023-12-31")
    ap.add_argument("--lookback", type=int, default=365)
    ap.add_argument("--skip-analyze", action="store_true")
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
    from app.enrichment.patent_features import _MAX_ABSTRACT_CHARS, build_prompt, validate_features

    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=args.concurrency + 2)

    async with pool.acquire() as conn:
        srows = await conn.fetch("SELECT id, ticker, name FROM stocks WHERE ticker = ANY($1::text[])", tickers)
        stock_by_id = {r["id"]: {"ticker": r["ticker"], "name": r["name"]} for r in srows}
        sel = await conn.fetch(
            """
            SELECT rd.id AS raw_document_id, rd.external_id AS application_no,
                   rd.stock_id, pd.patent_title
            FROM raw_documents rd JOIN patent_raw_details pd ON pd.raw_document_id = rd.id
            WHERE rd.source_name = $1 AND rd.stock_id = ANY($2::bigint[])
              AND pd.application_date BETWEEN $3 AND $4
              AND pd.llm_status = 'pending'
            ORDER BY rd.stock_id, pd.application_date DESC
            """,
            args.source_name, list(stock_by_id.keys()),
            date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
    patents = [{"raw_document_id": r["raw_document_id"], "application_no": r["application_no"],
                "stock_id": r["stock_id"], "title": r["patent_title"]} for r in sel]
    if args.limit:
        patents = patents[: args.limit]
    print(f"[select] {len(patents)} pending patents in scope {tickers} {args.start}..{args.end}")
    if not patents:
        print("nothing to enrich (all done?)")
        return 0

    # 2) abstracts from BigQuery -> extra_payload
    from google.cloud import bigquery  # type: ignore
    bq = bigquery.Client(project=project)
    ids = [p["application_no"] for p in patents]
    abstracts: dict[str, str] = {}
    for i in range(0, len(ids), 5000):  # chunk the IN-list
        chunk = ids[i : i + 5000]
        sql = f"""
        SELECT application_number,
          (SELECT a.text FROM UNNEST(abstract_localized) a
             ORDER BY CASE LOWER(a.language) WHEN 'ko' THEN 0 WHEN 'en' THEN 1 ELSE 2 END
             LIMIT 1) AS abstract
        FROM `{BQ_TABLE}`
        WHERE country_code='KR' AND application_number IN UNNEST(@ids)
        """
        job = bq.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", chunk)]))
        for r in job.result():
            if r["abstract"] and not abstracts.get(r["application_number"]):
                abstracts[r["application_number"]] = r["abstract"]
    print(f"[bq] abstracts found for {len(abstracts)}/{len(patents)}")
    async with pool.acquire() as conn:
        await conn.executemany(
            "UPDATE patent_raw_details SET extra_payload = extra_payload || $2::jsonb WHERE raw_document_id = $1",
            [(p["raw_document_id"], json.dumps({"abstract": abstracts[p["application_no"]]}, ensure_ascii=False))
             for p in patents if abstracts.get(p["application_no"])],
        )

    # 3) concurrent Gemini enrichment
    sem = asyncio.Semaphore(args.concurrency)
    tally = {"success": 0, "failed": 0, "skipped": 0, "in_tok": 0, "out_tok": 0}
    writes: list[tuple] = []
    done = 0

    async def worker(p: dict) -> None:
        nonlocal done
        abstract = (abstracts.get(p["application_no"]) or "")[:_MAX_ABSTRACT_CHARS]
        title = (p["title"] or "").strip()
        if not title and not abstract:
            writes.append((p["raw_document_id"], None, "skipped"))
            tally["skipped"] += 1
            return
        async with sem:
            res = await _call_with_retry(build_prompt(title, abstract), model=model, api_key=api_key)
        if res.get("ok"):
            try:
                feats = validate_features(json.loads(res["text"]))
                writes.append((p["raw_document_id"], json.dumps(feats, ensure_ascii=False), "success"))
                tally["success"] += 1
                tally["in_tok"] += res.get("in_tok", 0)
                tally["out_tok"] += res.get("out_tok", 0)
            except Exception:
                writes.append((p["raw_document_id"], None, "failed"))
                tally["failed"] += 1
        else:
            writes.append((p["raw_document_id"], None, "failed"))
            tally["failed"] += 1
        done += 1
        if done % 250 == 0:
            print(f"  ...{done}/{len(patents)}  ok={tally['success']} fail={tally['failed']}", flush=True)

    # Process in chunks and COMMIT each chunk, so an interrupted run (timeout /
    # background kill on long jobs) keeps its finished work — a re-run skips it via
    # the llm_status='pending' filter. A single end-of-run commit would lose
    # everything on any interruption.
    _PERSIST_CHUNK = 500

    async def _flush() -> None:
        if not writes:
            return
        batch = writes[:]
        writes.clear()
        async with pool.acquire() as conn:
            await conn.executemany(
                "UPDATE patent_raw_details SET llm_features = $2::jsonb, llm_status = $3 WHERE raw_document_id = $1",
                batch,
            )

    for i in range(0, len(patents), _PERSIST_CHUNK):
        await asyncio.gather(*(worker(p) for p in patents[i : i + _PERSIST_CHUNK]))
        await _flush()
    cost = tally["in_tok"] * PRICE_IN_PER_M / 1e6 + tally["out_tok"] * PRICE_OUT_PER_M / 1e6
    print(f"[enrich] success={tally['success']} failed={tally['failed']} skipped={tally['skipped']}")
    print(f"[cost] in_tok={tally['in_tok']:,} out_tok={tally['out_tok']:,}  measured ${cost:.4f}")

    # 5) analyzer signal per stock
    signals = []
    if not args.skip_analyze:
        import dataclasses
        from app.analyzers.config import PatentRuleConfig
        from app.analyzers.patent.analyzer import PatentAnalyzer
        from app.analyzers.patent.indicators import compute_indicators
        from app.evidence_loaders.patent_loader import PatentEvidenceLoader
        from signal_alpha_data_access.repositories import RawDetailRepository

        cfg = dataclasses.replace(PatentRuleConfig.from_env(), lookback_days=args.lookback)
        as_of = date.fromisoformat(args.as_of)
        print("\n" + "=" * 64)
        print(f"ANALYZER SIGNALS (as_of={as_of}, lookback={args.lookback}d)")
        print("=" * 64)
        async with pool.acquire() as conn:
            repo = RawDetailRepository(conn)
            loader = PatentEvidenceLoader(repo, lookback_days=args.lookback)
            analyzer = PatentAnalyzer(cfg)
            for sid, meta in stock_by_id.items():
                evidence = await loader.load(stock_id=sid, stock_code=meta["ticker"], as_of=as_of)
                result = await analyzer.analyze(meta["ticker"], evidence)
                ind = compute_indicators(evidence[0].metadata.get("rows", []), as_of=as_of, lookback_days=args.lookback)
                print(f"\n### {meta['ticker']} {meta['name']}")
                print(f"  direction={result.direction}  score={result.score:+.3f}  data_status={result.data_status}")
                print(f"  filings(window)={ind.total} recent={ind.recent_count} prior={ind.prior_count} momentum={ind.momentum_ratio}")
                print(f"  LLM-enriched={ind.llm_enriched_count} mean_sig={ind.mean_significance} max_sig={ind.max_significance}")
                print(f"  summary: {result.summary}")
                signals.append({"ticker": meta["ticker"], "name": meta["name"], "direction": result.direction,
                                "score": result.score, "total": ind.total, "llm_enriched": ind.llm_enriched_count,
                                "mean_significance": ind.mean_significance})

    await pool.close()
    if args.out:
        Path(args.out).write_text(json.dumps({"tally": tally, "cost_usd": cost, "signals": signals},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
