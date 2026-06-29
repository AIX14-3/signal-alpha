"""Build BigQuery assignee LIKE patterns for a stock universe (solves the
TICKER_BQ_PATTERNS bottleneck) and pre-screen by actual patent presence.

For each stock: ask Gemini for its Google-Patents assignee name(s) → patterns,
then ONE BigQuery probe counts KR applications (2016-2023) + sample matched
assignee names per pattern. Stocks below ``--min-apps`` are dropped (no/too-few
patents). Output:
  - ``--out`` patent_assignee_patterns.json = {ticker: [patterns]}  (kept stocks)
  - ``--review`` patterns_review.json = full per-ticker counts/samples for HUMAN
    review (catch over-broad patterns / subsidiary contamination before loading).

Read-only (Gemini + BigQuery; no DB writes). Needs GEMINI_API_KEY (.env) + ADC.

    uv run --with google-cloud-bigquery python scripts/build_patent_patterns.py \
        --universe universe.json --out scripts/patent_assignee_patterns.json \
        --review patterns_review.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"

PROMPT = """You map a Korean listed company to its patent ASSIGNEE name(s) as they appear in
Google Patents (BigQuery patents-public-data, assignee_harmonized.name — English/romanized, UPPERCASE).

Company: {name} (KRX ticker {ticker}).

Return JSON only: {{"english_name": "...", "patterns": ["%FULL NAME%", ...]}}
Rules:
- patterns are SQL LIKE on UPPER(assignee name); use %...% with the company's FULL
  distinctive name (e.g. "%SAMSUNG ELECTRONICS%", NOT bare "%SAMSUNG%").
- EXCLUDE separate affiliates/subsidiaries (for 삼성전자 do NOT match SAMSUNG SDI/DISPLAY/SDS).
- 1-3 patterns max, ASCII uppercase only. If you don't know, return {{"english_name":"","patterns":[]}}.
"""


async def _gen_patterns(universe, api_key, model, concurrency):
    from app.clients.gemini_client import GeminiJsonClient

    client = GeminiJsonClient(api_key=api_key, model=model)
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, dict] = {}

    async def one(stock):
        t, name = stock["ticker"], stock.get("name", "")
        async with sem:
            try:
                res = await client.generate_json(PROMPT.format(name=name, ticker=t))
            except Exception as exc:  # noqa: BLE001
                out[t] = {"name": name, "english_name": "", "patterns": [], "error": str(exc)[:80]}
                return
        pats = res.get("patterns") if isinstance(res, dict) else None
        pats = [str(p).upper() for p in pats if p] if isinstance(pats, list) else []
        out[t] = {"name": name, "english_name": (res or {}).get("english_name", ""), "patterns": pats}

    await asyncio.gather(*(one(s) for s in universe))
    return out


def _probe_bq(all_patterns, project, start_year=2016, end_year=2023):
    from google.cloud import bigquery  # type: ignore

    client = bigquery.Client(project=project)
    sql = f"""
    SELECT p AS pattern,
           COUNT(DISTINCT t.application_number) AS n,
           ARRAY_AGG(DISTINCT UPPER(a.name) IGNORE NULLS LIMIT 5) AS samples
    FROM `{BQ_TABLE}` t, UNNEST(t.assignee_harmonized) a, UNNEST(@patterns) p
    WHERE t.country_code = 'KR'
      AND t.filing_date BETWEEN @start AND @end
      AND UPPER(a.name) LIKE p
    GROUP BY p
    """
    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("patterns", "STRING", all_patterns),
        bigquery.ScalarQueryParameter("start", "INT64", start_year * 10000 + 101),
        bigquery.ScalarQueryParameter("end", "INT64", end_year * 10000 + 1231),
    ]))
    return {r["pattern"]: {"n": int(r["n"]), "samples": list(r["samples"] or [])} for r in job.result()}


async def _run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build + pre-screen patent assignee patterns")
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--min-apps", type=int, default=30, help="min KR applications (2016-2023) to keep a stock")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--bq-project", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY required (.env).")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    project = args.bq_project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_BQ_PROJECT

    universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    print(f"[gemini] generating patterns for {len(universe)} stocks ({model})...")
    gen = await _gen_patterns(universe, api_key, model, args.concurrency)

    all_patterns = sorted({p for v in gen.values() for p in v["patterns"]})
    print(f"[bq] probing {len(all_patterns)} distinct patterns...")
    probe = _probe_bq(all_patterns, project) if all_patterns else {}

    review = []
    kept: dict[str, list[str]] = {}
    for t, v in gen.items():
        pat_info = [{"pattern": p, **probe.get(p, {"n": 0, "samples": []})} for p in v["patterns"]]
        best = max((pi["n"] for pi in pat_info), default=0)
        keep = best >= args.min_apps
        if keep:
            kept[t] = v["patterns"]
        entry = {"ticker": t, "name": v["name"], "english_name": v.get("english_name", ""),
                 "best_n": best, "kept": keep, "patterns": pat_info}
        if "error" in v:
            entry["error"] = v["error"]
        review.append(entry)

    review.sort(key=lambda r: -r["best_n"])
    Path(args.out).write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.review).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[result] kept {len(kept)}/{len(universe)} stocks (>= {args.min_apps} apps)")
    print(f"{'ticker':<8}{'best_n':>8}  kept  name / english")
    for r in review:
        flag = "OK " if r["kept"] else "drop"
        print(f"  {r['ticker']:<8}{r['best_n']:>8}  {flag}  {r['name'][:12]} / {r['english_name'][:24]}")
    print(f"\n[out] patterns -> {args.out}\n[review] -> {args.review}")
    print("⚠ REVIEW: over-broad patterns (huge best_n vs company size) or wrong samples = subsidiary contamination.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
