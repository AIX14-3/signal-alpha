#!/usr/bin/env python
"""Bridge 1 (PEAD) core experiment: does a PIT search NOWCAST of the earnings
SURPRISE add PRE-announcement directional value to the post-earnings drift, and
does fusing it with the realized surprise help? (Direction label = fwd return sign.)

Chain under test (audit-confirmed priors):
  search(DataLab) ─nowcast→ SUE(revenue surprise) ─PEAD→ post-announcement drift dir.

Panel: one row per earnings announcement (t, q_ann). q_rev = q_ann-1 is the reported
quarter (KR 잠정실적 lags ~45d). All predictors are PIT relative to the announcement d:
  • search_abn(q_rev)   z of that quarter's search vs firm trailing-4q  (KNOWN before d)
  • SUE(q_rev)          firm-standardized rev-YoY accel, REVEALED at d (tradeable for
                        the drift trade entered at d+1, since drift starts after d)
Targets: market-adjusted (KS11) excess drift over [d+1, d+1+h], h in {20,60,120}.

Experiments (all: non-overlapping cross-sectional folds keyed by announcement quarter,
within-quarter permutation p, BH-FDR q=0.10 over ALL cells, within-firm rank-IC,
per-period IC, decile long-short economics net of cost, era split 2016-20 / 2021-23):

  (1a) REALIZED  SUE(q_rev)           → drift        anchor: PEAD exists?
  (1b) NOWCAST   OOS predicted-SUE    → drift        pre-announcement lead value?
       (walk-forward OLS search→SUE trained STRICTLY on past announcement quarters)
  (contrast) DIRECT search_abn        → drift        search's own drift channel
  (5) FUSION  z(realized SUE)+z(search_abn), main-effects → drift   does fusion help?

    PYTHONIOENCODING=utf-8 python scripts/pead_nowcast_fusion.py \
        --disclosures dart_disclosures.json --search-csv stockname_daily_krx250.csv \
        --dart dart_krx250.csv --prices-csv prices_krx250.csv \
        --benchmark-csv prices_kospi15_2016_2026.csv --winsor=-0.9,3.0 --nperm 1000
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.evaluation import benjamini_hochberg, within_firm_ic  # noqa: E402
from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.event_study_leadlag import pearson  # noqa: E402
from scripts.search_pead import earnings_dates, fwd_excess  # noqa: E402
from scripts.search_pead_surprise import revenue_sue  # noqa: E402
from scripts.search_to_fundamental import (  # noqa: E402
    load_revenue,
    quarter_search,
    rev_yoy,
    search_features,
)

random.seed(42)
SPLIT = 2021 * 4          # qi boundary between eras
COST_SIDES = [0.0, 0.0015, 0.0030]


# ----------------------------- metric helpers --------------------------------
def spearman(xs, ys):
    """Rank-IC (Spearman) via Pearson on ranks; nan on degenerate input."""
    n = len(xs)
    if n < 3:
        return float("nan")
    rx = _ranks(xs)
    ry = _ranks(ys)
    return pearson(rx, ry)


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def xs_rankic_perm(byq, nperm, min_n=6):
    """Mean cross-sectional rank-IC over quarters + within-quarter shuffle two-sided p."""
    panels = {q: ([r[0] for r in rows], [r[1] for r in rows])
              for q, rows in byq.items() if len(rows) >= min_n}

    def mean_ic(perm=False):
        ics = []
        for s, f in panels.values():
            ff = f[:]
            if perm:
                random.shuffle(ff)
            ic = spearman(s, ff)
            if ic is not None and ic == ic:
                ics.append(ic)
        return statistics.mean(ics) if ics else float("nan")

    obs = mean_ic()
    if obs != obs or not panels:
        return obs, float("nan"), len(panels)
    ge = sum(1 for _ in range(nperm) if abs(mean_ic(perm=True)) >= abs(obs))
    return obs, (ge + 1) / (nperm + 1), len(panels)


def era_ic(byq, lo=None, hi=None):
    sub = {q: [(r[0], r[1]) for r in rows] for q, rows in byq.items()
           if (lo is None or q >= lo) and (hi is None or q < hi)}
    return xs_rankic_perm(sub, 1)[0]


def within_firm(byq):
    """Pooled within-firm rank-IC: needs (pred, drift, ticker) rows."""
    s, r, sid = [], [], []
    for rows in byq.values():
        for p, d, t in rows:
            s.append(p); r.append(d); sid.append(t)
    if len(s) < 20:
        return float("nan")
    return within_firm_ic(np.array(s, float), np.array(r, float), np.array(sid))


def per_period_signflip(byq, min_n=6):
    ics = [spearman([r[0] for r in rows], [r[1] for r in rows])
           for rows in byq.values() if len(rows) >= min_n]
    ics = [x for x in ics if x == x]
    if not ics:
        return float("nan"), float("nan"), 0
    pos = sum(1 for x in ics if x > 0) / len(ics)
    m = statistics.mean(ics)
    return m, pos, len(ics)


def quarterly_ls(byq, k_frac=0.1, min_names=10):
    out = []
    for q in sorted(byq):
        vals = [(k, d) for k, d, *_ in byq[q] if k is not None and d is not None]
        if len(vals) < min_names:
            continue
        vals.sort(key=lambda x: x[0])
        k = max(1, int(len(vals) * k_frac))
        bot = statistics.mean(d for _, d in vals[:k])
        top = statistics.mean(d for _, d in vals[-k:])
        out.append((q, top - bot))
    return out


def ls_econ(qrets):
    ls = [v for _, v in qrets]
    n = len(ls)
    if n < 4:
        return None
    mean, sd = statistics.mean(ls), statistics.pstdev(ls)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    hit = sum(1 for v in ls if v > 0) / n
    e0 = [v for q, v in qrets if q < SPLIT]
    e1 = [v for q, v in qrets if q >= SPLIT]
    return {"n": n, "mean": mean, "t": t, "hit": hit,
            "m0": statistics.mean(e0) if e0 else float("nan"),
            "m1": statistics.mean(e1) if e1 else float("nan")}


# ----------------------------- OOS nowcast -----------------------------------
def ols_fit(X, y):
    """Return coefficients (with intercept) via least squares. X: list[list], y: list."""
    A = np.array([[1.0] + row for row in X], float)
    b = np.array(y, float)
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    return coef


def ols_pred(coef, row):
    return float(coef[0] + sum(c * v for c, v in zip(coef[1:], row)))


def walkforward_nowcast(rows, feat_idx, sue_idx, qann_idx, min_train=40):
    """OOS predicted-SUE per row via OLS(search features -> realized SUE), trained
    STRICTLY on announcement quarters < the test quarter (purged, PIT). Returns
    {q_ann: [(sue_hat, ...)]} aligned; rows missing features/label are skipped in train."""
    quarters = sorted({r[qann_idx] for r in rows})
    preds = {}  # row-id -> sue_hat
    for qt in quarters:
        train = [r for r in rows if r[qann_idx] < qt
                 and all(r[i] is not None for i in feat_idx) and r[sue_idx] is not None]
        if len(train) < min_train:
            continue
        X = [[r[i] for i in feat_idx] for r in train]
        y = [r[sue_idx] for r in train]
        if np.std(y) == 0:
            continue
        coef = ols_fit(X, y)
        for rid, r in enumerate(rows):
            if r[qann_idx] == qt and all(r[i] is not None for i in feat_idx):
                preds[rid] = ols_pred(coef, [r[i] for i in feat_idx])
    return preds


# ----------------------------- main ------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosures", default="dart_disclosures.json")
    ap.add_argument("--search-csv", default="stockname_daily_krx250.csv")
    ap.add_argument("--dart", default="dart_krx250.csv")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--horizons", default="20,60,120")
    ap.add_argument("--skip", type=int, default=1)
    ap.add_argument("--winsor", default="")
    ap.add_argument("--nperm", type=int, default=1000)
    args = ap.parse_args()

    w = None
    if args.winsor:
        lo, hi = (float(x) for x in args.winsor.split(","))
        w = (lo, hi)

    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    disclosures = json.loads(Path(args.disclosures).read_text(encoding="utf-8"))
    edates = earnings_dates(disclosures)
    sq = quarter_search(args.search_csv)
    abn, syoy = search_features(sq)
    ry = rev_yoy(load_revenue(args.dart), winsor=w)
    sue = revenue_sue(ry)
    prices = load_prices_csv(args.prices_csv)
    bench = load_prices_csv(args.benchmark_csv).get(args.benchmark)
    if bench is None:
        raise SystemExit(f"benchmark {args.benchmark} not in {args.benchmark_csv}")

    # Build the announcement panel with all PIT predictors + multi-horizon drifts.
    # row = dict of fields; we index into a list for the nowcast helper.
    # fields: [q_ann, ticker, s_abn, s_abn_lag, s_yoy, sue, drift20, drift60, drift120]
    HCOL = {h: 6 + i for i, h in enumerate([20, 60, 120])}
    rows = []
    n_have_sue = n_have_abn = 0
    for t, ds in edates.items():
        if t not in prices:
            continue
        for d in ds:
            q_ann = d.year * 4 + (d.month - 1) // 3
            q_rev = q_ann - 1
            s_abn = abn.get(t, {}).get(q_rev)
            s_abn_l = abn.get(t, {}).get(q_rev - 1)
            s_yoy = syoy.get(t, {}).get(q_rev)
            su = sue.get(t, {}).get(q_rev)
            drifts = [fwd_excess(prices[t], bench, d, skip=args.skip, win=h)
                      for h in [20, 60, 120]]
            if all(x is None for x in drifts):
                continue
            rows.append([q_ann, t, s_abn, s_abn_l, s_yoy, su] + drifts)
            n_have_sue += su is not None
            n_have_abn += s_abn is not None

    n_stocks = len({r[1] for r in rows})
    n_sue_stocks = len({r[1] for r in rows if r[5] is not None})
    print(f"이벤트(실적공시) {len(rows)}건 · {n_stocks}종목 | SUE보유 {n_have_sue}건/{n_sue_stocks}종목 "
          f"· search_abn보유 {n_have_abn}건 | winsor={args.winsor or 'none'} | NPERM={args.nperm}")
    print(f"드리프트 창 [d+{args.skip}, d+{args.skip}+h], h={horizons} · 시장조정(KS11)\n")

    # OOS nowcast: predicted-SUE from [s_abn, s_abn_lag, s_yoy] trained on past quarters.
    feat_idx = [2, 3, 4]
    sue_hat = walkforward_nowcast(rows, feat_idx, sue_idx=5, qann_idx=0)
    n_hat = len(sue_hat)
    # correlation of OOS predicted vs realized SUE (nowcast fidelity), pooled
    pv = [(sue_hat[i], rows[i][5]) for i in sue_hat if rows[i][5] is not None]
    fid = spearman([a for a, _ in pv], [b for _, b in pv]) if len(pv) > 3 else float("nan")
    print(f"[나우캐스트 적합] OOS 예측SUE 생성 {n_hat}건 | 예측SUE↔실현SUE rank-corr={fid:+.3f} (n={len(pv)})\n")

    cells = []  # (label, h, byq_for_ic[(pred,drift)], byq_for_wf[(pred,drift,ticker)])

    def build(predfn, h):
        dcol = HCOL[h]
        byq_ic, byq_wf, byq_ls = defaultdict(list), defaultdict(list), defaultdict(list)
        for rid, r in enumerate(rows):
            p = predfn(rid, r)
            dr = r[dcol]
            if p is None or dr is None:
                continue
            byq_ic[r[0]].append((p, dr))
            byq_wf[r[0]].append((p, dr, r[1]))
            byq_ls[r[0]].append((p, dr))
        return byq_ic, byq_wf, byq_ls

    predictors = {
        "1a realizedSUE": lambda rid, r: r[5],
        "1b nowcastSUE(OOS)": lambda rid, r: sue_hat.get(rid),
        "direct search_abn": lambda rid, r: r[2],
    }

    results = []  # for BH-FDR: (label, h, ic, p, nq, wf, e0, e1, sign_pos, econ)
    for h in horizons:
        for name, fn in predictors.items():
            byq_ic, byq_wf, byq_ls = build(fn, h)
            ic, p, nq = xs_rankic_perm(byq_ic, args.nperm)
            wf = within_firm(byq_wf)
            e0 = era_ic(byq_ic, hi=SPLIT)
            e1 = era_ic(byq_ic, lo=SPLIT)
            _, spos, npd = per_period_signflip(byq_ic)
            econ = ls_econ(quarterly_ls(byq_ls))
            results.append((name, h, ic, p, nq, wf, e0, e1, spos, econ))

    # FUSION: z(realized SUE) + z(search_abn), equal-weight main-effects.
    def zmap(idx):
        vals = [r[idx] for r in rows if r[idx] is not None]
        mu, sd = statistics.mean(vals), statistics.pstdev(vals)
        return mu, sd if sd > 0 else 1.0

    mu_s, sd_s = zmap(5)
    mu_a, sd_a = zmap(2)

    def fuse(rid, r):
        if r[5] is None or r[2] is None:
            return None
        return (r[5] - mu_s) / sd_s + (r[2] - mu_a) / sd_a

    for h in horizons:
        byq_ic, byq_wf, byq_ls = build(fuse, h)
        ic, p, nq = xs_rankic_perm(byq_ic, args.nperm)
        wf = within_firm(byq_wf)
        e0 = era_ic(byq_ic, hi=SPLIT)
        e1 = era_ic(byq_ic, lo=SPLIT)
        _, spos, npd = per_period_signflip(byq_ic)
        econ = ls_econ(quarterly_ls(byq_ls))
        results.append(("5 FUSION SUE+search", h, ic, p, nq, wf, e0, e1, spos, econ))

    # BH-FDR across ALL cells at q=0.10.
    survive = benjamini_hochberg([r[3] for r in results], q=0.10)

    print(f"  {'cell':>22} {'h':>4} {'rankIC':>8} {'perm_p':>8} {'FDR':>4} {'분기':>4} "
          f"{'wfIC':>7} {'16-20':>7} {'21-23':>7} {'pos%':>5} | {'LS%':>7} {'t':>6} {'net30t':>7}")
    for (name, h, ic, p, nq, wf, e0, e1, spos, econ), surv in zip(results, survive):
        if econ:
            ls, tt = econ["mean"], econ["t"]
            net30 = (econ["mean"] - 4 * 0.0030 * 100) / (econ.get("_sd", 1) or 1) if False else None
            # net t at 30bp per side: subtract 4*30bp*100 from mean, keep sd via t scaling
            nm = econ["mean"] - 4 * 0.0030 * 100.0
            net_t = nm / (econ["mean"] / econ["t"]) if econ["t"] not in (0.0,) and econ["t"] == econ["t"] else float("nan")
        else:
            ls = tt = net_t = float("nan")
        flag = "yes" if surv else "."
        print(f"  {name:>22} {h:>4} {ic:>+8.3f} {p:>8.4f} {flag:>4} {nq:>4} "
              f"{wf:>+7.3f} {e0:>+7.3f} {e1:>+7.3f} {spos:>5.2f} | "
              f"{ls:>+7.2f} {tt:>+6.2f} {net_t:>+7.2f}")

    print("\n  rankIC=분기횡단면 rank-IC 평균 · perm_p=분기내셔플 양측 · FDR=BH q0.10 생존 · "
          "wfIC=within-firm rank-IC")
    print("  LS%=분기 decile 롱숏(상위10%-하위10%) gross · t=분기 t · net30t=30bp/편도 비용후 근사 t")
    print("  판정: 방향신호 = rankIC>0 & FDR생존 & wfIC 동부호(정적특성 아님) & era 양쪽 일관 & 비용후 net_t>~2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
