#!/usr/bin/env python
"""In-process driver for the source-agnostic feature×label search.

Replaces the ``run_fusion_sweep.py`` subprocess shim: builds a pre-registered grid
for one source and runs it through the in-process engine (resume ledger + sweep-wide
BH-FDR + held-out confirmation + within-firm gate), writing a git-ignored ledger and
report.

Offline (no credentials):
    python scripts/run_feature_search.py --source synthetic --grid small --perm 100
    python scripts/run_feature_search.py --source datalab-demo --grid small --perm 100

Real data (env-gated — asyncpg + data-access + .env DATABASE_URL + CSVs):
    # the validated edge: hiring → next-quarter revenue nowcast
    python scripts/run_feature_search.py --source revenue --grid small \
        --tickers 005930,000660 --revenue-csv revenue_dart.csv --perm 200
    # fusion of every source → direction (honest longshot)
    python scripts/run_feature_search.py --source fusion --grid demo \
        --tickers 005930,000660 --prices-csv prices.csv --benchmark KS11 --perm 200

A gated source (missing DATABASE_URL/CSV) is recorded as a GATE in the report — the
grid never crashes; it tells you exactly what to unblock.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ml.research.adapters import DATASET_SPECS  # noqa: E402
from app.ml.research.search import run_sweep  # noqa: E402
from app.ml.research.search_grid import build_grid  # noqa: E402


def _extra_from_args(args) -> dict:
    """Panel knobs for the grid's ``extra`` (only what the chosen source reads)."""
    if args.source == "synthetic":
        return {"n_stocks": 60, "n_dates": 60, "noise": args.noise}
    if args.source == "datalab-demo":
        return {"n_stocks": args.stocks_real, "weeks": args.weeks,
                "signal_step": args.signal_step, "lookback": args.lookback}
    # DB-gated sources: pass through the credentials/window/label knobs verbatim.
    extra: dict = {
        "tickers": args.tickers, "start": args.start, "end": args.end,
        "benchmark": args.benchmark or "", "prices_csv": args.prices_csv or "",
        "revenue_csv": args.revenue_csv or "", "lookback": args.lookback,
        "signal_step": args.signal_step, "min_obs": args.min_obs,
        "min_cross_section": args.min_cross_section, "feature_set": args.feature_set,
        "xs_normalize": args.xs_normalize, "precise_rematch": args.precise_rematch,
        "fusion_sources": args.fusion_sources, "target": args.target,
        # offline (MCP-dump) revenue path — no DATABASE_URL needed.
        "stocks_json": args.stocks_json or "", "postings_jsonl": args.postings_jsonl or "",
        "signal_step_days": args.revenue_signal_step_days,
        "label_mode": args.label_mode,
        "patent_json": args.patent_json or "",
    }
    if args.fusion_min_sources is not None:
        extra["fusion_min_sources"] = args.fusion_min_sources
    return extra


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Source-agnostic feature×label search")
    p.add_argument("--source", default="synthetic", choices=sorted(DATASET_SPECS))
    p.add_argument("--grid", default="small", choices=["small", "demo", "full"])
    p.add_argument("--out", default="search_out", help="output dir (ledger + report; git-ignored)")
    p.add_argument("--universe", default="")
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--perm", type=int, default=200,
                   help="permutation iterations (100=explore, 200=confirm)")
    p.add_argument("--q", type=float, default=0.10, help="BH-FDR level")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--no-holdout", action="store_true")
    # synthetic / demo knobs
    p.add_argument("--noise", type=float, default=1.0)
    p.add_argument("--stocks-real", type=int, default=8)
    p.add_argument("--weeks", type=int, default=120)
    # db knobs
    p.add_argument("--tickers", default="005930,000660,035420")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2023-12-31")
    p.add_argument("--benchmark", default=None)
    p.add_argument("--prices-csv", default=None)
    p.add_argument("--revenue-csv", default=None)
    # offline revenue-nowcast dumps (source=revenue-offline; MCP stocks/HIRING dumps)
    p.add_argument("--stocks-json", default=None, help="stocks 덤프 JSON (revenue-offline)")
    p.add_argument("--postings-jsonl", default=None, help="HIRING 포스팅 JSONL (revenue-offline)")
    p.add_argument("--patent-json", default=None,
                   help="특허 스칼라 덤프 JSONL (patent-revenue-offline/fusion-revenue-offline)")
    p.add_argument("--label-mode", default="yoy", choices=["yoy", "surprise"],
                   help="revenue 라벨: yoy(LEVEL 성장) 또는 surprise(within-firm SUE)")
    p.add_argument("--revenue-signal-step-days", type=int, default=0,
                   help="revenue-offline 월별 신호(0=quarterly; 30=월별 3스냅샷/분기, 횡단면↑). "
                        "누수 방지 embargo 는 revenue 라벨 horizon 이 담당")
    p.add_argument("--lookback", type=int, default=90)
    p.add_argument("--signal-step", type=int, default=20)
    p.add_argument("--min-obs", type=int, default=2)
    p.add_argument("--min-cross-section", type=int, default=6)
    p.add_argument("--feature-set", default="volume")
    p.add_argument("--xs-normalize", default="none", choices=["none", "rank", "zscore"])
    p.add_argument("--precise-rematch", action="store_true")
    p.add_argument("--fusion-sources", default="patent,hiring,datalab")
    p.add_argument("--fusion-min-sources", type=int, default=None)
    p.add_argument("--target", default="direction",
                   choices=["direction", "abs_return", "realized_vol"],
                   help="hiring/patent magnitude label (abs_return / realized_vol)")
    args = p.parse_args(argv)

    universe = args.universe or args.source
    cells = build_grid(source=args.source, size=args.grid, universe=universe,
                       seed=args.seed, extra=_extra_from_args(args))
    print(f"[search] {len(cells)} cells  source={args.source} grid={args.grid} perm={args.perm}")
    summary = run_sweep(
        cells, out_dir=args.out, n_folds=args.folds, n_perm=args.perm, q=args.q,
        resume=not args.no_resume, holdout=not args.no_holdout,
    )
    print(f"[search] ran={summary['ran_this_call']}  ok={summary['ok']}  "
          f"gate={summary['gate']}  skip={summary['skip']}  "
          f"FDR_survivors={summary['fdr_survivors']}  "
          f"confirmed={summary['holdout_confirmed']}")
    print(f"[search] report → {summary['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
