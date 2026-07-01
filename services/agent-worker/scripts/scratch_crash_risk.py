#!/usr/bin/env python
"""SCRATCH (uncommitted) — audit gap ②: does abnormal search predict future CRASH RISK?

Search = attention/magnitude proxy; the robust DataLab finding is attention→magnitude
(future vol/volume). Crash risk is a DIRECTIONAL-magnitude (tail asymmetry) target, not a
mean-direction target, so it's a fair extension. Chen&Chen 2024 JEF / Chen-Hong-Stein 2001.

Labels (firm-specific weekly returns W = ln(1+ε), ε = market-model residual with ±1
lead/lag vs KS11), computed over a non-overlapping WEEK-BLOCK:
  NCSKEW = −[n(n−1)^1.5 ΣW³] / [(n−1)(n−2)(ΣW²)^1.5]       (higher = more crash-prone)
  DUVOL  = ln[(n_up−1)Σ_down W² / (n_down−1)Σ_up W²]       (higher = more crash-prone)
  CRASH  = #weeks with W < mean − 3.09σ  (Hutton 2009 dummy), per-block count

Predictor = mean PIT abnormal name-search over the PRIOR block. Cross-sectional IC per
block (pred vs next-block label), mean + t across blocks, within-block-shuffle
permutation + BH-FDR across {label × cell}. Conditioned on liquidity / retail terciles
(retail = 저가주+회전율 프록시, KRX individual panel blocked — see conditional_reversal).

Read-only, local CSVs only. No DB, no commits.

    PYTHONIOENCODING=utf-8 uv run python scripts/scratch_crash_risk.py \
        --prices-csv prices_krx250.csv --search-csv stockname_daily_krx250.csv \
        --benchmark-csv prices_kospi15_2016_2026.csv --universe krx_top250.json --label krx250
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.period_keyword_dataset import load_keyword_series  # noqa: E402
from scripts.event_study_leadlag import pearson  # noqa: E402
from scripts.scratch_conditional_reversal import (  # noqa: E402
    amihud_by_ticker,
    marcap_by_ticker,
    retail_proxy_by_ticker,
    terciles,
)
from scripts.search_to_magnitude import ffill, load_px, rolling_z, search_by_date  # noqa: E402

random.seed(42)
NPERM = 2000
BLOCK = 26          # weeks per label/predictor block (semiannual); override via --block
MINWK = 12          # min firm-weeks in a block to compute a label
CRASH_K = 3.09      # Hutton 2009 crash threshold (σ below mean)


def iso_week(dstr):
    y, m, d = int(dstr[:4]), int(dstr[5:7]), int(dstr[8:10])
    iy, iw, _ = date(y, m, d).isocalendar()
    return iy * 100 + iw


def weekly_returns(closes_by_date):
    """daily {date:close} -> {week_key: logret} using each week's last close."""
    last = {}
    for d in sorted(closes_by_date):
        last[iso_week(d)] = closes_by_date[d]
    wks = sorted(last)
    out = {}
    for i in range(1, len(wks)):
        c0, c1 = last[wks[i - 1]], last[wks[i]]
        if c0 > 0 and c1 > 0:
            out[wks[i]] = math.log(c1 / c0)
    return out


def firm_specific(wr, mr):
    """Residual W = ln(1+ε), ε from OLS r_i = a + b1 m[-1] + b2 m + b3 m[+1]. Full-sample β."""
    import numpy as np
    wks = sorted(w for w in wr if w in mr)
    rows, keys = [], []
    for w in wks:
        wm1 = _prev_week(mr, w)
        wp1 = _next_week(mr, w)
        if wm1 is None or wp1 is None:
            continue
        rows.append((wr[w], mr[wm1], mr[w], mr[wp1]))
        keys.append(w)
    if len(rows) < 30:
        return {}
    arr = np.array(rows)
    y = arr[:, 0]
    X = np.column_stack([np.ones(len(arr)), arr[:, 1], arr[:, 2], arr[:, 3]])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return {k: math.log(1.0 + max(r, -0.999)) for k, r in zip(keys, resid)}


def _prev_week(mr, w):
    cand = [x for x in mr if x < w]
    return max(cand) if cand else None


def _next_week(mr, w):
    cand = [x for x in mr if x > w]
    return min(cand) if cand else None


def ncskew(ws):
    n = len(ws)
    s2 = sum(w * w for w in ws)
    s3 = sum(w ** 3 for w in ws)
    if n < 3 or s2 <= 0:
        return None
    return -(n * (n - 1) ** 1.5 * s3) / ((n - 1) * (n - 2) * s2 ** 1.5)


def duvol(ws):
    mu = statistics.mean(ws)
    down = [w for w in ws if w < mu]
    up = [w for w in ws if w >= mu]
    nd, nu = len(down), len(up)
    if nd < 2 or nu < 2:
        return None
    sd = sum(w * w for w in down)
    su = sum(w * w for w in up)
    if su <= 0 or sd <= 0:
        return None
    return math.log(((nu - 1) * sd) / ((nd - 1) * su))


def crash_count(ws):
    if len(ws) < 3:
        return None
    mu, sd = statistics.mean(ws), statistics.pstdev(ws)
    if sd <= 0:
        return None
    return sum(1 for w in ws if w < mu - CRASH_K * sd)


LABELS = {"NCSKEW": ncskew, "DUVOL": duvol, "CRASH": crash_count}


def blocks_of(all_weeks):
    """Non-overlapping BLOCK-week windows: list of (block_weeks,) in order."""
    return [all_weeks[i:i + BLOCK] for i in range(0, len(all_weeks) - BLOCK + 1, BLOCK)]


def build_panels(fw, abn_week, keep, labelfn):
    """Per adjacent block pair (k -> k+1): cross-section of (prior-block mean abn, next-block label).
    Returns weekly_ic list + perm panels (centered vectors)."""
    all_weeks = sorted({w for t in keep for w in fw.get(t, {})})
    blks = blocks_of(all_weeks)
    ics, panels = [], []
    for k in range(len(blks) - 1):
        pblk, lblk = set(blks[k]), blks[k + 1]
        xs, ys = [], []
        for t in keep:
            wser = fw.get(t, {})
            lws = [wser[w] for w in lblk if w in wser]
            if len(lws) < MINWK:
                continue
            lab = labelfn(lws)
            if lab is None:
                continue
            avals = [abn_week[t][w] for w in pblk if w in abn_week.get(t, {})]
            if not avals:
                continue
            xs.append(statistics.mean(avals)); ys.append(lab)
        if len(xs) < 6:
            continue
        ic = pearson(xs, ys)
        if ic is not None:
            ics.append(ic)
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cx = [v - mx for v in xs]; cy = [v - my for v in ys]
        dx = sum(v * v for v in cx) ** 0.5; dy = sum(v * v for v in cy) ** 0.5
        if dx > 0 and dy > 0:
            panels.append((cx, cy, dx * dy))
    return ics, panels


def ic_mean_t(ics):
    n = len(ics)
    if n < 3:
        return float("nan"), float("nan"), n
    m, sd = statistics.mean(ics), statistics.pstdev(ics)
    return m, (m / (sd / n ** 0.5) if sd > 0 else float("nan")), n


def perm_p(panels, obs):
    if len(panels) < 3 or obs != obs:
        return float("nan")
    hits = 0
    for _ in range(NPERM):
        vals = []
        for cx, cy, denom in panels:
            yy = cy[:]; random.shuffle(yy)
            vals.append(sum(a * b for a, b in zip(cx, yy)) / denom)
        if abs(statistics.mean(vals)) >= abs(obs):
            hits += 1
    return (hits + 1) / (NPERM + 1)


def bh_fdr(pvals):
    idx = [i for i, p in enumerate(pvals) if p == p]
    order = sorted(idx, key=lambda i: pvals[i]); mm = len(order); alpha = 0.05
    k_star = 0
    for rank, i in enumerate(order, 1):
        if pvals[i] <= (rank / mm) * alpha:
            k_star = rank
    q = {i: 1.0 for i in range(len(pvals))}; run = 1.0
    for rank in range(mm, 0, -1):
        i = order[rank - 1]; run = min(run, mm / rank * pvals[i]); q[i] = run
    rank_of = {i: r for r, i in enumerate(order, 1)}
    return q, {i: rank_of.get(i, 10**9) <= k_star for i in range(len(pvals))}, k_star, mm


def main() -> int:
    global BLOCK, MINWK
    p = argparse.ArgumentParser()
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--search-csv", required=True)
    p.add_argument("--benchmark-csv", required=True)
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--universe", default="")
    p.add_argument("--label", default="uni")
    p.add_argument("--block", type=int, default=26, help="weeks per block (26=semiannual, 13=quarterly)")
    a = p.parse_args()
    BLOCK = a.block
    MINWK = max(8, a.block // 2)

    data = load_px(a.prices_csv)
    bench = load_px(a.benchmark_csv)
    if a.benchmark not in bench:
        print(f"벤치마크 {a.benchmark} 없음: {list(bench)[:5]}"); return 1
    mr = weekly_returns(bench[a.benchmark]["close"])

    # firm-specific weekly residual returns
    fw = {}
    for t, stk in data.items():
        w = firm_specific(weekly_returns(stk["close"]), mr)
        if w:
            fw[t] = w

    # PIT abnormal search → weekly (mean of daily abn within each ISO week)
    rbd = search_by_date(load_keyword_series(a.search_csv))
    abn_daily = {t: rolling_z(stk["td"], ffill(stk["td"], rbd[t]))
                 for t, stk in data.items() if t in rbd and rbd[t]}
    abn_week = {}
    for t, ad in abn_daily.items():
        wk = defaultdict(list)
        for d, z in ad.items():
            wk[iso_week(d)].append(z)
        abn_week[t] = {w: statistics.mean(v) for w, v in wk.items()}

    common = [t for t in fw if t in abn_week and abn_week[t]]
    print(f"[{a.label}] {len(common)} stocks (firm-specific weekly, BLOCK={BLOCK}w)\n")

    # conditioning subsets (same axes as reversal)
    marcap = marcap_by_ticker(a.universe)
    amihud = amihud_by_ticker(data)
    retail = retail_proxy_by_ticker(data, marcap)
    size_lo, size_hi = terciles(marcap, common)
    illq_lo, illq_hi = terciles(amihud, common)
    ret_lo, ret_hi = terciles(retail, common)
    subsets = {"ALL": set(common), "small": size_lo, "large": size_hi,
               "illiquid": illq_hi, "liquid": illq_lo, "hi-retail": ret_hi, "lo-retail": ret_lo}
    subsets = {k: v for k, v in subsets.items() if len(v) >= 6}
    print("조건부 부분집합:", {k: len(v) for k, v in subsets.items()}, "\n")

    cells = []  # [label, cell, ic, t, nblk, perm_p]
    for lname, fn in LABELS.items():
        for sname, keep in subsets.items():
            ics, panels = build_panels(fw, abn_week, keep, fn)
            m, t, nb = ic_mean_t(ics)
            pp = perm_p(panels, m)
            cells.append([lname, sname, m, t, nb, pp])

    pvals = [c[5] for c in cells]
    q, rej, k_star, mm = bh_fdr(pvals)
    print("=== 검색 abnormal(선행 블록) → 미래 폭락위험(다음 블록) 횡단면 IC ===")
    print("    (양수 IC = 고어텐션이 미래 crash 위험↑ 예측)")
    print(f"  trials={len(cells)} valid={mm} | Bonferroni 0.05/{mm}={0.05/max(mm,1):.4f} "
          f"| p-floor {1/(NPERM+1):.4f}")
    print(f"  {'label':>8} {'cell':>10} {'IC':>8} {'t':>7} {'nblk':>5} {'perm_p':>7} {'BH_q':>6} {'rej':>4}")
    for i, (ln, sn, m, t, nb, pp) in enumerate(cells):
        flag = "YES" if rej.get(i) else ""
        print(f"  {ln:>8} {sn:>10} {m:>+8.3f} {t:>+7.2f} {nb:>5} {pp:>7.4f} {q[i]:>6.3f} {flag:>4}")
    print(f"  BH 생존(q<=0.05): {k_star}/{mm}")
    print("\n판정선: 고개인·저유동 셀서 IC>0 & perm BH 생존 => 검색=미래 폭락위험 예측(리스크 레이어). 아니면 무신호.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
