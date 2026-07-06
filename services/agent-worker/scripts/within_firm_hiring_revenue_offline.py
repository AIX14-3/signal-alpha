#!/usr/bin/env python
"""[채용 Step1] within-firm 게이트 — MCP-오프라인 실행(DB 접속 없이 덤프로).

이 머신엔 DATABASE_URL 이 없어 `within_firm_hiring_revenue.py`(load_from_env, asyncpg)를 못 쓴다.
대신 Supabase MCP 로 뽑아 로컬에 저장한 덤프 + OpenDART 매출 CSV 로 동일 게이트를 돌린다.
로직은 `hiring_mcp_offline`(precise rematch 재현) + `within_firm_gate`(동일). confirmed 런과 동형.

입력:
  --stocks-json     : [[id,ticker,name,short_name], ...]  (stocks 전체 덤프)
  --postings-jsonl  : 한 줄당 {"source_name","observed_date","duty_groups"}  (HIRING 전량)
  --revenue-csv     : fundamentals_dart 가 만든 revenue_dart.csv
  --universe-json   : ["005380", ...]  (clean 티커; 없으면 --tickers)

실행(작업 디렉터리 services/agent-worker, DART 키만 있으면 uv/DB 불필요):
  python scripts/within_firm_hiring_revenue_offline.py \\
      --stocks-json <scratch>/stocks.json \\
      --postings-jsonl <scratch>/hiring_postings.jsonl \\
      --revenue-csv <scratch>/revenue_dart.csv \\
      --universe-json <scratch>/universe_clean15.json \\
      --feature-set volume+duty --n-perm 200
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.ml.research.hiring_mcp_offline import build_dataset_from_dumps  # noqa: E402
from app.ml.research.within_firm_gate import gate_report, render_gate  # noqa: E402


def _load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stocks-json", required=True)
    p.add_argument("--postings-jsonl", required=True)
    p.add_argument("--revenue-csv", required=True)
    p.add_argument("--universe-json", help="clean 티커 JSON 배열")
    p.add_argument("--tickers", help="콤마구분(universe-json 없을 때)")
    p.add_argument("--feature-set", default="volume+duty",
                   choices=["volume", "duty", "volume+duty"])
    p.add_argument("--lookback", type=int, default=90)
    p.add_argument("--min-obs", type=int, default=2)
    p.add_argument("--min-cross-section", type=int, default=6)
    p.add_argument("--model", default="decision_tree")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-perm", type=int, default=200)
    p.add_argument("--min-obs-per-firm", type=int, default=2)
    p.add_argument("--sector-map", help="{ticker: 섹터} JSON — 주면 라벨을 (섹터×시점) 내 중립화")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 방어(한글·기호)
    except (AttributeError, ValueError):
        pass
    args = _parse_args(argv)

    stocks_rows = [tuple(r) for r in json.load(open(args.stocks_json, encoding="utf-8"))]
    postings = _load_jsonl(args.postings_jsonl)
    if args.universe_json:
        tickers = json.load(open(args.universe_json, encoding="utf-8"))
    elif args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    else:
        raise SystemExit("--universe-json 또는 --tickers 필요")

    ds = build_dataset_from_dumps(
        stocks_rows=stocks_rows, postings=postings, revenue_csv=args.revenue_csv,
        tickers=tickers, feature_set=args.feature_set, lookback_days=args.lookback,
        min_observations=args.min_obs, min_cross_section=args.min_cross_section,
    )
    if args.sector_map:
        from app.ml.research.fundamentals_dataset import sector_neutralize_label
        sector_by_ticker = json.load(open(args.sector_map, encoding="utf-8"))
        ticker_by_sid = {int(sid): t for sid, t, *_ in stocks_rows}
        sector_by_stock = {
            sid: sector_by_ticker.get(ticker_by_sid.get(sid))
            for sid in (int(x) for x in np.unique(ds.stock_ids))
        }
        ds = sector_neutralize_label(ds, sector_by_stock,
                                     min_cross_section=args.min_cross_section)
        print(f"[sector-neutral] 라벨 (섹터×시점) 중립화 적용 → samples={len(ds)}")

    print(f"[revenue] samples={len(ds)}  features={len(ds.feature_names)}  "
          f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
          f"quarters={len(np.unique(ds.dates)) if len(ds) else 0}  "
          f"dropped={dict(ds.dropped)}")
    if len(ds) == 0:
        raise SystemExit("No samples — revenue_csv/postings 확인")

    report = gate_report(
        ds, model_name=args.model, n_folds=args.folds, seed=args.seed,
        n_perm=args.n_perm, min_obs_per_firm=args.min_obs_per_firm,
    )
    print(render_gate(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
