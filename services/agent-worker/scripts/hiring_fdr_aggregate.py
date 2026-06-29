"""Stage 2c: pool permutation p-values across feature-sets x horizons x models and
apply ONE Benjamini-Hochberg FDR correction (the statistically correct family scope).

Reads the per-feature-set permutation CSVs written by ``bakeoff --permute-csv``
(columns: horizon,model,obs_rankIC,p_value,null_mean) and reports:
  * the full family BH outcome (how many of N tests survive FDR at alpha),
  * the top cells by raw p, with their BH q-values,
  * a clear verdict line.

Run (from services/agent-worker):
    uv run --extra dev python scripts/hiring_fdr_aggregate.py \
        --csv volume=.../perm_vol.csv duty=.../perm_duty.csv volume+duty=.../perm_both.csv \
        --alpha 0.05
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.research.stats import benjamini_hochberg  # noqa: E402

# baselines are not hypotheses about the features -> exclude from the FDR family
_SKIP = {"baseline_majority", "baseline_stratified"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="+", required=True,
                    help="label=path entries, e.g. volume=perm_vol.csv duty=perm_duty.csv")
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args()

    rows = []
    for entry in args.csv:
        label, _, path = entry.partition("=")
        if not path:
            label, path = Path(entry).stem, entry
        if not Path(path).exists():
            print(f"WARN missing {path}")
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                model = r["model"]
                if model in _SKIP:
                    continue
                p = r["p_value"]
                if p in ("", "nan"):
                    continue
                pv = float(p)
                if math.isnan(pv):
                    continue
                rows.append({
                    "feature_set": label,
                    "horizon": int(r["horizon"]),
                    "model": model,
                    "obs": float(r["obs_rankIC"]),
                    "p": pv,
                })

    if not rows:
        raise SystemExit("no p-values found")

    pvals = [r["p"] for r in rows]
    bh = benjamini_hochberg(pvals, alpha=args.alpha)
    for r, q, rej in zip(rows, bh.qvalues, bh.rejected):
        r["q"] = q
        r["reject"] = rej

    n = len(rows)
    n_perm_floor = min(pvals)
    print(f"=== BH-FDR across the full family (alpha={args.alpha}) ===")
    print(f"family size N = {n} tests "
          f"({len({r['feature_set'] for r in rows})} feature-sets x "
          f"{len({r['horizon'] for r in rows})} horizons x "
          f"{len({r['model'] for r in rows})} models)")
    print(f"BH rejections (survive FDR): {bh.n_rejected}")
    print(f"min raw p observed = {n_perm_floor:.4f} "
          f"(permutation p-floor ~= 1/(n_perm+1))")

    rows.sort(key=lambda d: d["p"])
    print("\n=== top 15 cells by raw p ===")
    print(f"{'feat':<12}{'h':>4}{'model':>16}{'obs_rankIC':>12}{'raw_p':>8}{'BH_q':>8}{'BH?':>5}")
    for r in rows[:15]:
        print(f"{r['feature_set']:<12}{r['horizon']:>4}{r['model']:>16}"
              f"{r['obs']:>+12.3f}{r['p']:>8.3f}{r['q']:>8.3f}{'YES' if r['reject'] else '':>5}")

    verdict = (
        "NO cell survives BH-FDR -> hiring (volume/duty/volume+duty) shows no "
        "significant directional signal at N=57."
        if bh.n_rejected == 0
        else f"{bh.n_rejected} cell(s) survive BH-FDR -> investigate further."
    )
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
