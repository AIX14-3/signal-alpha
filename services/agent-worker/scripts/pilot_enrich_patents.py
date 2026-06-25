"""Cost/quality pilot for patent LLM enrichment (Gemini 2.5 Flash-Lite).

Measures the REAL per-call token usage of the patent enricher prompt so a
full-range cost can be projected before committing. Samples N already-loaded
GOOGLE_PATENTS patents, fetches their Korean abstracts from BigQuery (the live
backfill stored title only), runs the exact production prompt
(``app.enrichment.patent_features.build_prompt``) through Gemini while capturing
``usageMetadata``, then reports avg tokens, measured cost, and a projection.

Measurement only — does NOT write llm_features to the DB.

  uv run --with google-cloud-bigquery python scripts/pilot_enrich_patents.py --sample 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

# gemini-2.5-flash-lite paid-tier text pricing (USD / 1M tokens), verified 2026-06.
PRICE_IN_PER_M = 0.10
PRICE_OUT_PER_M = 0.40

# Projection targets (unique applications already loaded).
RANGE_2021_2023 = 28835
RANGE_2016_2023 = 61999


def _call_gemini(prompt: str, *, model: str, api_key: str, timeout: float = 60.0) -> dict:
    """One Gemini call. Returns {ok, text, in_tok, out_tok, thought_tok, status}."""
    url = ENDPOINT.format(model=model, key=api_key)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "retryable": exc.code in {429, 500, 502, 503, 504}}
    except (URLError, TimeoutError) as exc:
        return {"ok": False, "status": str(exc), "retryable": True}

    usage = payload.get("usageMetadata", {})
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "status": "no-candidate", "retryable": True,
                "in_tok": usage.get("promptTokenCount", 0)}
    return {
        "ok": True,
        "text": text,
        "in_tok": usage.get("promptTokenCount", 0),
        "out_tok": usage.get("candidatesTokenCount", 0),
        "thought_tok": usage.get("thoughtsTokenCount", 0),
    }


async def _call_with_retry(prompt: str, *, model, api_key, attempts=4) -> dict:
    last = None
    for i in range(attempts):
        res = await asyncio.to_thread(_call_gemini, prompt, model=model, api_key=api_key)
        if res.get("ok") or not res.get("retryable"):
            return res
        last = res
        await asyncio.sleep(1.5 ** i)
    return last or {"ok": False, "status": "exhausted"}


async def _run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Patent enrichment cost/quality pilot")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--pace", type=float, default=4.5,
                    help="seconds between calls (stay under free-tier ~15 RPM)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--bq-project", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ["DATABASE_URL"]
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is required for the pilot.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    project = args.bq_project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_BQ_PROJECT

    # 1) sample loaded patents (title only) from the target range
    from datetime import date
    import asyncpg
    conn = await asyncpg.connect(database_url)
    rows = await conn.fetch(
        """
        SELECT rd.external_id AS application_no, pd.patent_title, rd.stock_id
        FROM raw_documents rd JOIN patent_raw_details pd ON pd.raw_document_id = rd.id
        WHERE rd.source_name = 'GOOGLE_PATENTS'
          AND pd.application_date BETWEEN $1 AND $2
        ORDER BY random() LIMIT $3
        """,
        date.fromisoformat(args.start), date.fromisoformat(args.end), args.sample,
    )
    await conn.close()
    sample = [{"application_no": r["application_no"], "title": r["patent_title"]} for r in rows]
    print(f"[db] sampled {len(sample)} patents ({args.start}..{args.end})")

    # 2) fetch abstracts from BigQuery for exactly those applications
    from google.cloud import bigquery  # type: ignore
    client = bigquery.Client(project=project)
    app_ids = [s["application_no"] for s in sample]
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
    with_abs = sum(1 for s in sample if abstracts.get(s["application_no"]))
    print(f"[bq] abstracts found for {with_abs}/{len(sample)} patents")

    # 3) run the production prompt through Gemini, capturing token usage
    from app.enrichment.patent_features import _MAX_ABSTRACT_CHARS, build_prompt, validate_features

    results: list[dict] = []
    # Sequential + pacing: free-tier flash-lite caps ~15 RPM, so bursts get 429s.
    for i, s in enumerate(sample):
        abstract = (abstracts.get(s["application_no"]) or "")[:_MAX_ABSTRACT_CHARS]
        prompt = build_prompt(s["title"] or "", abstract)
        res = await _call_with_retry(prompt, model=model, api_key=api_key)
        rec = {"application_no": s["application_no"], "had_abstract": bool(abstract),
               "ok": res.get("ok", False), "in_tok": res.get("in_tok", 0),
               "out_tok": res.get("out_tok", 0), "thought_tok": res.get("thought_tok", 0)}
        if res.get("ok"):
            try:
                rec["features"] = validate_features(json.loads(res["text"]))
            except Exception:
                rec["features"] = None
        else:
            rec["status"] = res.get("status")
        results.append(rec)
        if (i + 1) % 20 == 0:
            ok_so_far = sum(1 for r in results if r["ok"])
            print(f"  ...{i + 1}/{len(sample)} done, {ok_so_far} ok", flush=True)
        if i < len(sample) - 1:
            await asyncio.sleep(args.pace)

    # 4) aggregate + project
    ok = [r for r in results if r["ok"]]
    fail = len(results) - len(ok)
    n = len(ok) or 1
    sum_in = sum(r["in_tok"] for r in ok)
    sum_out = sum(r["out_tok"] + r["thought_tok"] for r in ok)  # bill thinking as output
    avg_in, avg_out = sum_in / n, sum_out / n
    cost_in = sum_in * PRICE_IN_PER_M / 1e6
    cost_out = sum_out * PRICE_OUT_PER_M / 1e6
    cost_total = cost_in + cost_out
    per_call = cost_total / n

    print("\n" + "=" * 60)
    print(f"PILOT RESULT  (model={model})")
    print("=" * 60)
    print(f"  calls ok / fail        : {len(ok)} / {fail}")
    print(f"  abstract coverage      : {with_abs}/{len(sample)} ({100*with_abs/len(sample):.0f}%)")
    print(f"  avg input tokens/call  : {avg_in:.0f}")
    print(f"  avg output tokens/call : {avg_out:.0f}  (incl. thinking)")
    print(f"  measured pilot cost    : ${cost_total:.4f}  (in ${cost_in:.4f} + out ${cost_out:.4f})")
    print(f"  cost per call          : ${per_call:.6f}")
    print("  --- projection (assumes same avg) ---")
    print(f"  2021-2023 ({RANGE_2021_2023:,})  : ${per_call * RANGE_2021_2023:.2f}")
    print(f"  2016-2023 ({RANGE_2016_2023:,})  : ${per_call * RANGE_2016_2023:.2f}")

    samples = [r for r in ok if r.get("features")][:3]
    if samples:
        print("\n  --- sample enrichment output (quality check) ---")
        for r in samples:
            f = r["features"]
            print(f"  {r['application_no']} (abstract={r['had_abstract']}): "
                  f"sig={f['significance']} novelty={f['novelty']} "
                  f"stage={f['commercialization_stage']}")
            print(f"     rationale: {f['rationale'][:80]}")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "model": model, "sample": len(sample), "ok": len(ok), "fail": fail,
            "abstract_coverage": with_abs, "avg_in": avg_in, "avg_out": avg_out,
            "pilot_cost_usd": cost_total, "per_call_usd": per_call,
            "proj_2021_2023_usd": per_call * RANGE_2021_2023,
            "proj_2016_2023_usd": per_call * RANGE_2016_2023,
            "results": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[out] written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
