#!/usr/bin/env python
"""PEAD test: does pre-announcement SEARCH predict the post-earnings DRIFT?

R showed search abn (quarter Q) cross-sectionally leads next-quarter revenue, but the
search→PRICE chain was flat at the naive horizon — because revenue is largely priced
when announced. The tradeable residual, if any, is the POST-ANNOUNCEMENT DRIFT (PEAD):
the part of the reaction the market doesn't immediately price. This script tests whether
the search that foresaw the fundamental also foresees that drift.

For each earnings disclosure (잠정실적) at date d, predictor = the freshest quarterly
search abn KNOWN before d (lag quarters back), target = market-adjusted (KS11) excess
return over a forward DRIFT window starting the day AFTER d. Cross-sectional IC per
announcement-quarter, within-quarter permutation + era split — the same bar as R.

    uv run python scripts/search_pead.py --disclosures dart_disclosures.json \
        --search-csv stockname_daily_krx250.csv --prices-csv prices_krx250.csv \
        --benchmark-csv prices_kospi15_2016_2026.csv --benchmark KS11 --drift 20
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.event_study_leadlag import pearson  # noqa: E402
from scripts.search_to_fundamental import quarter_search, search_features  # noqa: E402

random.seed(42)

# Earnings-announcement disclosure titles (provisional quarterly/annual results).
EARNINGS_PAT = ["잠정실적", "영업(잠정)실적", "매출액또는손익", "영업실적", "결산실적"]


def earnings_dates(disclosures: dict) -> dict[str, list[date]]:
    """{ticker: sorted unique earnings-announcement dates} from 실적 disclosures."""
    out: dict[str, set] = defaultdict(set)
    for ticker, rows in disclosures.items():
        for r in rows:
            title = r.get("report_nm") or ""
            rcept = (r.get("rcept_dt") or "")[:8]
            if len(rcept) < 8:
                continue
            if any(p in title for p in EARNINGS_PAT):
                out[ticker].add(date(int(rcept[:4]), int(rcept[4:6]), int(rcept[6:8])))
    return {t: sorted(ds) for t, ds in out.items()}


def fwd_excess(ps, bench, d: date, *, skip: int, win: int):
    """Market-adjusted return over [entry+skip, entry+skip+win], entry = first td >= d."""
    i = bisect.bisect_left(ps.dates, d)
    a = i + skip
    b = a + win
    if i >= len(ps.dates) or b >= len(ps.closes):
        return None
    c0, c1 = ps.closes[a], ps.closes[b]
    if c0 <= 0 or c1 <= 0:
        return None
    d0, d1 = ps.dates[a], ps.dates[b]
    j0, j1 = bench._index.get(d0), bench._index.get(d1)
    if j0 is None or j1 is None or bench.closes[j0] <= 0 or bench.closes[j1] <= 0:
        return None
    return ((c1 / c0 - 1.0) - (bench.closes[j1] / bench.closes[j0] - 1.0)) * 100.0


def xs_ic_perm(byq, nperm):
    """Mean cross-sectional IC over quarters + within-quarter shuffle two-sided perm p."""
    panels = {}
    for qi, rows in byq.items():
        if len(rows) >= 6:
            s = [r[0] for r in rows]
            f = [r[1] for r in rows]
            panels[qi] = (s, f)

    def mean_ic(perm=False):
        ics = []
        for _qi, (s, f) in panels.items():
            ff = f[:]
            if perm:
                random.shuffle(ff)
            ic = pearson(s, ff)
            if ic is not None:
                ics.append(ic)
        return statistics.mean(ics) if ics else float("nan")

    obs = mean_ic()
    if obs != obs or not panels:
        return obs, float("nan"), len(panels)
    ge = sum(1 for _ in range(nperm) if abs(mean_ic(perm=True)) >= abs(obs))
    return obs, (ge + 1) / (nperm + 1), len(panels)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosures", default="dart_disclosures.json")
    ap.add_argument("--search-csv", default="stockname_daily_krx250.csv")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--drift", type=int, default=20, help="drift window in trading days")
    ap.add_argument("--skip", type=int, default=1, help="trading days after d to start (skip jump)")
    ap.add_argument("--pred-lags", default="1,2", help="quarters back for the search predictor")
    ap.add_argument("--nperm", type=int, default=2000)
    args = ap.parse_args()

    disclosures = json.loads(Path(args.disclosures).read_text(encoding="utf-8"))
    edates = earnings_dates(disclosures)
    abn, _ = search_features(quarter_search(args.search_csv))
    prices = load_prices_csv(args.prices_csv)
    bench = load_prices_csv(args.benchmark_csv).get(args.benchmark)
    if bench is None:
        raise SystemExit(f"benchmark {args.benchmark} not in {args.benchmark_csv}")

    n_ann = sum(len(v) for v in edates.values())
    print(f"실적공시 {n_ann}건, {len(edates)}종목 | drift [d+{args.skip}, d+{args.skip+args.drift}] "
          f"시장조정(KS11) | NPERM={args.nperm}\n")

    SPLIT = 2021 * 4
    print(f"  {'predictor':>22} {'IC평균':>8} {'perm_p':>8} {'분기수':>6}   {'2016-20':>9} {'2021-23':>9}")
    cells = []
    for lag in [int(x) for x in args.pred_lags.split(",") if x.strip()]:
        byq, byq0, byq1 = defaultdict(list), defaultdict(list), defaultdict(list)
        for t, ds in edates.items():
            if t not in prices or t not in abn:
                continue
            for d in ds:
                q_ann = d.year * 4 + (d.month - 1) // 3
                pv = abn[t].get(q_ann - lag)
                if pv is None:
                    continue
                tv = fwd_excess(prices[t], bench, d, skip=args.skip, win=args.drift)
                if tv is None:
                    continue
                byq[q_ann].append((pv, tv))
                (byq0 if q_ann < SPLIT else byq1)[q_ann].append((pv, tv))
        obs, p2, nq = xs_ic_perm(byq, args.nperm)
        e0, _, _ = xs_ic_perm(byq0, 1)
        e1, _, _ = xs_ic_perm(byq1, 1)
        cells.append((f"search_abn(Q-{lag})", obs, p2, nq, e0, e1))
        print(f"  {f'search_abn(Q-{lag})':>22} {obs:>+8.3f} {p2:>8.4f} {nq:>6}   "
              f"{e0:>+9.3f} {e1:>+9.3f}")

    print("\n  판정: PEAD(발표후 드리프트)를 검색이 예측하면 IC>0 & perm_p<0.05 & era 일관.")
    print("  무신호면 = 검색의 펀더멘털 정보는 발표 시점에 효율적으로 반영(거래 드리프트 없음).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
