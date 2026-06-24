#!/usr/bin/env python
"""Sweep the DataLab bake-off across many horizons (1d .. 2y) in one run.

The harness (`app.ml.bakeoff`) evaluates ONE horizon per process, so this loops
the grid. For long horizons it raises ``--signal-step`` to the horizon length so
each sample's outcome window does NOT overlap its neighbours -- otherwise
autocorrelation inflates Rank-IC and a non-signal looks like a signal (exactly
the trap that made 2021 h=10 look promising before it collapsed on more data).

It prints each horizon's full bake-off table (so nothing is hidden) and then a
compact cross-horizon summary: per horizon, the sample count and the best
non-baseline model's Rank-IC + IC stability.

Usage:
    DATABASE_URL=... python scripts/sweep_datalab_horizons.py \
        --tickers 005930,000660,035420,... --start 2016-01-01 --end 2026-06-24 \
        --benchmark KS11 --prices-csv prices_kospi15_2016_2026.csv --out-dir sweep_out
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

# 1 day, 1 week, 1 month, 1 quarter, 1 half, 1 year, 2 years (trading sessions)
HORIZONS = [1, 5, 21, 63, 126, 252, 504]
LABELS = {1: "1d", 5: "1w", 21: "1mo", 63: "3mo", 126: "6mo", 252: "1y", 504: "2y"}


def signal_step_for(horizon: int) -> int:
    """Non-overlapping outcome windows for long horizons; weekly for short ones."""
    return max(5, horizon)


def run_one(args: argparse.Namespace, horizon: int, csv_path: Path) -> tuple[int, int]:
    step = signal_step_for(horizon)
    cmd = [
        sys.executable, "-m", "app.ml.bakeoff",
        "--source", "datalab-db",
        "--tickers", args.tickers,
        "--start", args.start,
        "--end", args.end,
        "--horizon", str(horizon),
        "--signal-step", str(step),
        "--folds", str(args.folds),
        "--csv", str(csv_path),
    ]
    if args.benchmark:
        cmd += ["--benchmark", args.benchmark]
    if args.prices_csv:
        cmd += ["--prices-csv", args.prices_csv]
    banner = f"[h={horizon} ({LABELS.get(horizon, horizon)})  step={step}]"
    print(f"\n{'=' * 72}\n{banner}  {' '.join(cmd)}\n{'=' * 72}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
    m = re.search(r"samples=(\d+)", proc.stdout)
    return (int(m.group(1)) if m else 0), proc.returncode


def best_rankic(csv_path: Path) -> tuple[str, float, float] | None:
    """Best non-baseline model by Rank-IC, with its IC std (stability)."""
    if not csv_path.exists():
        return None
    best: tuple[str, float, float] | None = None
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["model"].startswith("baseline"):
                continue
            try:
                ric = float(row["rank_ic_mean"])
                sd = float(row.get("ic_std", "nan"))
            except (ValueError, KeyError):
                continue
            if best is None or ric > best[1]:
                best = (row["model"], ric, sd)
    return best


def main() -> int:
    p = argparse.ArgumentParser(description="Multi-horizon DataLab-only bake-off sweep")
    p.add_argument("--tickers", required=True, help="comma-separated tickers")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default="2026-06-24")
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--prices-csv", default=None)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--out-dir", default="sweep_out")
    p.add_argument("--horizons", default=None, help="comma list override (sessions)")
    args = p.parse_args()

    horizons = [int(x) for x in args.horizons.split(",")] if args.horizons else HORIZONS
    outdir = Path(args.out_dir)
    outdir.mkdir(exist_ok=True)

    summary: list[tuple[int, int, tuple[str, float, float] | None, int]] = []
    for h in horizons:
        csv_path = outdir / f"results_h{h}.csv"
        samples, rc = run_one(args, h, csv_path)
        summary.append((h, samples, best_rankic(csv_path), rc))

    print("\n\n" + "=" * 72)
    print("CROSS-HORIZON SUMMARY (DataLab-only)")
    print("=" * 72)
    print(f"{'horizon':>10} {'samples':>9} {'best_model':>22} {'rankIC':>9} {'sd_IC':>8}")
    print("-" * 62)
    for h, samples, best, _rc in summary:
        lbl = f"{h}({LABELS.get(h, '')})"
        if best:
            model, ric, sd = best
            print(f"{lbl:>10} {samples:>9} {model:>22} {ric:>+9.3f} {sd:>+8.3f}")
        else:
            print(f"{lbl:>10} {samples:>9} {'(no result)':>22} {'n/a':>9} {'n/a':>8}")
    print(
        "\nRead: rankIC>0 with small sd_IC AND adequate samples = a real signal. "
        "Long horizons have FEW independent samples (step=horizon), so treat a high "
        "rankIC at a low sample count as noise, not signal."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
