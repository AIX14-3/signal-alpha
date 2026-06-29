"""Probe hand-curated assignee patterns against BigQuery (no Gemini).

Reads a candidates JSON [{ticker,name,market,patterns}], runs ONE BigQuery probe
for KR application counts + sample matched assignee names per pattern, prints a
per-ticker review, and writes the KEPT (>= --min-apps) set to the patterns config
(+ a universe JSON for seeding). Read-only on BigQuery; no DB writes.

    uv run --with google-cloud-bigquery python scripts/probe_patterns.py \
        --candidates candidates_curated.json \
        --out scripts/patent_assignee_patterns.json --universe-out universe_curated.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"


def _probe(patterns, project, start_year=2016, end_year=2023):
    from google.cloud import bigquery  # type: ignore

    client = bigquery.Client(project=project)
    sql = f"""
    SELECT p AS pattern,
           COUNT(DISTINCT t.application_number) AS n,
           ARRAY_AGG(DISTINCT UPPER(a.name) IGNORE NULLS LIMIT 6) AS samples
    FROM `{BQ_TABLE}` t, UNNEST(t.assignee_harmonized) a, UNNEST(@patterns) p
    WHERE t.country_code = 'KR'
      AND t.filing_date BETWEEN @start AND @end
      AND UPPER(a.name) LIKE p
    GROUP BY p
    """
    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("patterns", "STRING", patterns),
        bigquery.ScalarQueryParameter("start", "INT64", start_year * 10000 + 101),
        bigquery.ScalarQueryParameter("end", "INT64", end_year * 10000 + 1231),
    ]))
    return {r["pattern"]: {"n": int(r["n"]), "samples": list(r["samples"] or [])} for r in job.result()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Probe curated assignee patterns vs BigQuery")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--universe-out", required=True)
    ap.add_argument("--min-apps", type=int, default=50)
    ap.add_argument("--bq-project", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    project = args.bq_project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_BQ_PROJECT

    cands = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    all_patterns = sorted({p.upper() for c in cands for p in c["patterns"]})
    print(f"[bq] probing {len(all_patterns)} patterns for {len(cands)} candidates...")
    probe = _probe([p.upper() for p in all_patterns], project)

    kept: dict[str, list[str]] = {}
    universe = []
    print(f"\n{'ticker':<8}{'best_n':>8}  keep  name")
    rows = []
    for c in cands:
        pat_info = [{"pattern": p, **probe.get(p.upper(), {"n": 0, "samples": []})} for p in c["patterns"]]
        best = max((pi["n"] for pi in pat_info), default=0)
        rows.append((c, pat_info, best))
    for c, pat_info, best in sorted(rows, key=lambda x: -x[2]):
        keep = best >= args.min_apps
        if keep:
            kept[c["ticker"]] = c["patterns"]
            universe.append({"ticker": c["ticker"], "name": c["name"], "market": c["market"]})
        flag = "OK " if keep else "drop"
        print(f"  {c['ticker']:<8}{best:>8}  {flag}  {c['name']}")
        for pi in pat_info:
            print(f"        {pi['pattern']:<28} n={pi['n']:<6} {pi['samples'][:4]}")

    Path(args.out).write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.universe_out).write_text(json.dumps(universe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[result] kept {len(kept)}/{len(cands)} (>= {args.min_apps} apps)")
    print(f"[out] {args.out}  |  {args.universe_out}")
    print("⚠ REVIEW samples for contamination before loading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
