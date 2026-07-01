#!/usr/bin/env python
"""SCRATCH (uncommitted) — audit gap ①: CONDITIONAL attention REVERSAL.

Unconditional DataLab direction is NULL (expected: search = retail-attention proxy,
so the direction effect is conditional & sign-reversing and cancels when pooled). The
literature (Eom&Park 2021 KR, Lai 2022 TW, Da/Engelberg/Gao 2011) says high-attention
REVERSAL concentrates in the retail habitat — small / illiquid / high-retail stocks,
and is loser-driven. This tests exactly that, honestly:

  predictor  = PIT abnormal name-search (trailing rolling-z), point-in-time.
  target     = forward h-day CROSS-SECTIONAL excess return (demeaned within the cell).
               reversal ⇒ NEGATIVE IC (high attention → low forward return).
  cells      = ALL + per-axis terciles of {size, Amihud illiquidity, retail_frac},
               each also split winners/losers by prior h-day return sign.
  rigor      = per-period (non-overlapping week) IC mean + t (independent weeks),
               within-date-shuffle permutation (NPERM), BH-FDR across the cell×horizon
               family, and a WITHIN-FIRM (ticker-demeaned) variant to separate a
               tradeable timing effect from a static stock characteristic.

Read-only, local CSVs only. No DB, no commits.

    PYTHONIOENCODING=utf-8 uv run python scripts/scratch_conditional_reversal.py \
        --prices-csv prices_kosdaq.csv --search-csv stockname_daily_kosdaq.csv \
        --universe kosdaq_smallcap.json --label kosdaq [--retail-csv retail_share_kosdaq.csv]
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.event_study_leadlag import pearson  # noqa: E402
from scripts.search_to_magnitude import (  # noqa: E402
    ffill,
    load_px,
    rolling_z,
    search_by_date,
)

random.seed(42)
NPERM = 2000
HORIZONS = (5, 10, 20)  # trading days ≈ 1 / 2 / 4 weeks
ABN_HI = 1.0           # "high attention" threshold for the winner/loser mean-return diagnostic


# ---------- static per-ticker conditioning characteristics ----------
def marcap_by_ticker(universe_path):
    if not universe_path:
        return {}
    return {r["ticker"]: r.get("marcap_won") for r in json.load(open(universe_path, encoding="utf-8"))
            if r.get("marcap_won")}


def amihud_by_ticker(data):
    """Mean daily |return| / KRW-turnover (close*volume), ×1e9. Higher = more illiquid."""
    out = {}
    for t, stk in data.items():
        td, cl, vol = stk["td"], stk["close"], stk["vol"]
        vals = []
        for i in range(1, len(td)):
            c0, c1 = cl.get(td[i - 1]), cl.get(td[i])
            v = vol.get(td[i])
            if c0 and c1 and c0 > 0 and v and c1 * v > 0:
                vals.append(abs(c1 / c0 - 1.0) / (c1 * v) * 1e9)
        if len(vals) >= 60:
            out[t] = statistics.mean(vals)
    return out


def retail_by_ticker(path):
    if not path or not Path(path).exists():
        return {}
    import csv
    acc = defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                acc[r["ticker"]].append(float(r["retail_frac"]))
            except (ValueError, KeyError):
                pass
    return {t: statistics.mean(v) for t, v in acc.items() if v}


def retail_proxy_by_ticker(data, marcap):
    """Local retail-habitat proxy (KRX individual-ownership panel is blocked in this env).
    Composite = cheaper nominal price (저가주) + higher turnover velocity (일거래대금/시총).
    Both are validated retail proxies (Kumar 2009 lottery-stock preference); HIGHER = more
    retail. z(-log price) + z(turnover velocity)."""
    import math
    price, turn = {}, {}
    for t, stk in data.items():
        td, cl, vol = stk["td"], stk["close"], stk["vol"]
        closes = [cl[d] for d in td if cl.get(d)]
        if len(closes) < 60:
            continue
        price[t] = statistics.mean(closes)
        mc = marcap.get(t)
        if mc:
            tv = [cl[d] * vol[d] for d in td if cl.get(d) and vol.get(d)]
            if tv:
                turn[t] = statistics.mean(tv) / mc
    common = [t for t in price if t in turn]
    if len(common) < 9:
        return {}

    def zmap(d):
        vals = list(d.values())
        mu, sd = statistics.mean(vals), (statistics.pstdev(vals) or 1.0)
        return {t: (v - mu) / sd for t, v in d.items()}

    zlp = zmap({t: math.log(price[t]) for t in common})
    zt = zmap({t: turn[t] for t in common})
    return {t: (-zlp[t] + zt[t]) for t in common}


def terciles(char, tickers):
    """Return (low_set, high_set): bottom & top tercile of `char` among `tickers`."""
    scored = sorted((t for t in tickers if t in char), key=lambda t: char[t])
    n = len(scored)
    if n < 9:
        return set(), set()
    k = n // 3
    return set(scored[:k]), set(scored[-k:])


# ---------- forward / prior returns ----------
def fwd_ret(stk, d0, h):
    td, cl, pos = stk["td"], stk["close"], stk["pos"]
    i = pos.get(d0)
    if i is None or i + h >= len(td):
        return None
    c0, c1 = cl.get(td[i]), cl.get(td[i + h])
    return (c1 / c0 - 1.0) * 100.0 if c0 and c1 and c0 > 0 else None


def prior_ret(stk, d0, h):
    td, cl, pos = stk["td"], stk["close"], stk["pos"]
    i = pos.get(d0)
    if i is None or i - h < 0:
        return None
    c0, cp = cl.get(td[i]), cl.get(td[i - h])
    return (c0 / cp - 1.0) * 100.0 if c0 and cp and cp > 0 else None


# ---------- panel builder: per non-overlapping date, cross-sectional rows ----------
def build_panel(data, abn, keep, h, *, within_firm=False):
    """Return (weekly_ic, pool, perm_panels).
      weekly_ic  : list of per-week cross-sectional IC (independent weeks).
      pool       : list of (abn, xs_excess, prior) across all weeks (for winner/loser).
      perm_panels: list of (centered_abn, centered_xs, denom) for within-date shuffle perm.
    If within_firm: abn and xs_excess are additionally ticker-demeaned over the pool.
    """
    all_dates = sorted({d for s in data.values() for d in s["td"]})
    raw = []  # (date, ticker, abn, fwd, prior)
    for d0 in all_dates[::h]:
        rows = []
        for t in keep:
            stk = data.get(t)
            if stk is None or d0 not in abn.get(t, {}):
                continue
            fr = fwd_ret(stk, d0, h)
            if fr is None:
                continue
            rows.append((t, abn[t][d0], fr, prior_ret(stk, d0, h)))
        if len(rows) < 6:
            continue
        mean_f = statistics.mean(r[2] for r in rows)
        for t, a, fr, pr in rows:
            raw.append((d0, t, a, fr - mean_f, pr))

    if within_firm and raw:
        # subtract each ticker's own mean abn and mean xs (static-identity removal)
        amu = defaultdict(list); xmu = defaultdict(list)
        for _, t, a, xs, _ in raw:
            amu[t].append(a); xmu[t].append(xs)
        amean = {t: statistics.mean(v) for t, v in amu.items()}
        xmean = {t: statistics.mean(v) for t, v in xmu.items()}
        raw = [(d, t, a - amean[t], xs - xmean[t], pr) for d, t, a, xs, pr in raw]

    by_date = defaultdict(list)
    pool = []
    for d0, t, a, xs, pr in raw:
        by_date[d0].append((a, xs))
        pool.append((a, xs, pr))

    weekly_ic, perm_panels = [], []
    for d0, rows in by_date.items():
        if len(rows) < 6:
            continue
        xa = [r[0] for r in rows]; xy = [r[1] for r in rows]
        ic = pearson(xa, xy)
        if ic is not None:
            weekly_ic.append(ic)
        amu = statistics.mean(xa); ymu = statistics.mean(xy)
        ca = [v - amu for v in xa]; cy = [v - ymu for v in xy]
        da = sum(v * v for v in ca) ** 0.5; dy = sum(v * v for v in cy) ** 0.5
        if da > 0 and dy > 0:
            perm_panels.append((ca, cy, da * dy))
    return weekly_ic, pool, perm_panels


def ic_mean_t(weekly_ic):
    n = len(weekly_ic)
    if n < 3:
        return float("nan"), float("nan"), n
    m, sd = statistics.mean(weekly_ic), statistics.pstdev(weekly_ic)
    return m, (m / (sd / n ** 0.5) if sd > 0 else float("nan")), n


def perm_p(perm_panels, obs):
    if len(perm_panels) < 3:
        return float("nan")
    hits = 0
    for _ in range(NPERM):
        ics = []
        for ca, cy, denom in perm_panels:
            yy = cy[:]; random.shuffle(yy)
            ics.append(sum(a * b for a, b in zip(ca, yy)) / denom)
        if abs(statistics.mean(ics)) >= abs(obs):
            hits += 1
    return (hits + 1) / (NPERM + 1)


def winner_loser(pool):
    """pooled corr(abn, xs) and high-attn mean xs, split by prior-return sign."""
    out = {}
    for nm, sel in (("losers(prior<0)", lambda p: p < 0), ("winners(prior>=0)", lambda p: p >= 0)):
        grp = [(a, xs) for a, xs, pr in pool if pr is not None and sel(pr)]
        if len(grp) < 10:
            out[nm] = (float("nan"), float("nan"), len(grp), 0); continue
        ic = pearson([a for a, _ in grp], [xs for _, xs in grp])
        hi = [xs for a, xs in grp if a > ABN_HI]
        out[nm] = (ic if ic is not None else float("nan"),
                   statistics.mean(hi) if hi else float("nan"), len(grp), len(hi))
    return out


def bh_fdr(cells, pidx):
    m = len(cells); pv = [c[pidx] for c in cells]
    valid = [i for i in range(m) if pv[i] == pv[i]]  # drop NaN
    order = sorted(valid, key=lambda i: pv[i]); mm = len(order); alpha = 0.05
    k_star = 0
    for rank, idx in enumerate(order, 1):
        if pv[idx] <= (rank / mm) * alpha:
            k_star = rank
    q = {i: 1.0 for i in range(m)}; run = 1.0
    for rank in range(mm, 0, -1):
        idx = order[rank - 1]; run = min(run, mm / rank * pv[idx]); q[idx] = run
    rank_of = {idx: r for r, idx in enumerate(order, 1)}
    return q, {i: (rank_of.get(i, 10**9) <= k_star) for i in range(m)}, k_star, mm


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--search-csv", required=True)
    p.add_argument("--universe", default="")
    p.add_argument("--retail-csv", default="")
    p.add_argument("--label", default="uni")
    a = p.parse_args()

    data = load_px(a.prices_csv)
    rbd = search_by_date(load_keyword_series_local(a.search_csv))
    abn = {t: rolling_z(stk["td"], ffill(stk["td"], rbd[t]))
           for t, stk in data.items() if t in rbd and rbd[t]}
    common = [t for t in data if t in abn and abn[t]]
    data = {t: data[t] for t in common}
    print(f"[{a.label}] universe: {len(common)} stocks with PIT abn\n")

    # conditioning subsets
    marcap = marcap_by_ticker(a.universe)
    amihud = amihud_by_ticker(data)
    if a.retail_csv and Path(a.retail_csv).exists():
        retail, retail_src = retail_by_ticker(a.retail_csv), f"csv({a.retail_csv})"
    else:
        retail, retail_src = retail_proxy_by_ticker(data, marcap), "proxy(저가주+회전율)"
    size_lo, size_hi = terciles(marcap, common)          # lo=small, hi=large
    illq_lo, illq_hi = terciles(amihud, common)          # lo=liquid, hi=illiquid
    ret_lo, ret_hi = terciles(retail, common)            # lo=low-retail, hi=high-retail
    subsets = {"ALL": set(common), "small": size_lo, "large": size_hi,
               "illiquid": illq_hi, "liquid": illq_lo}
    if retail:
        subsets["hi-retail"] = ret_hi
        subsets["lo-retail"] = ret_lo
    subsets = {k: v for k, v in subsets.items() if len(v) >= 6}
    print(f"개인 축 소스: {retail_src if retail else '없음(시총·유동성만)'}")
    print("조건부 부분집합 크기:", {k: len(v) for k, v in subsets.items()})
    print()

    # ---- primary: per-period cross-sectional reversal IC, cell × horizon ----
    cells = []  # [subset, h, ic, t, nwk, perm_p, ic_wf]
    for sname, keep in subsets.items():
        for h in HORIZONS:
            wic, _pool, panels = build_panel(data, abn, keep, h)
            m, t, nwk = ic_mean_t(wic)
            pp = perm_p(panels, m) if m == m else float("nan")
            wic_wf, _, _ = build_panel(data, abn, keep, h, within_firm=True)
            m_wf, _, _ = ic_mean_t(wic_wf)
            cells.append([sname, h, m, t, nwk, pp, m_wf])

    q, rej, k_star, mm = bh_fdr(cells, pidx=5)
    ntrials = len(cells)
    print("=== 조건부 반전: 횡단면 IC (음수=반전) · 셀×horizon ===")
    print(f"  trials={ntrials} valid_perm={mm} | Bonferroni 0.05/{mm}={0.05/max(mm,1):.4f} "
          f"| p-floor {1/(NPERM+1):.4f}")
    print(f"  {'cell':>10} {'h':>3} {'IC':>8} {'t':>7} {'nwk':>4} {'perm_p':>7} "
          f"{'BH_q':>6} {'rej':>4} {'IC_wf':>8}")
    for i, (sn, h, m, t, nwk, pp, m_wf) in enumerate(cells):
        flag = "YES" if rej.get(i) else ""
        print(f"  {sn:>10} {h:>3} {m:>+8.3f} {t:>+7.2f} {nwk:>4} {pp:>7.4f} "
              f"{q[i]:>6.3f} {flag:>4} {m_wf:>+8.3f}")
    print(f"  BH 생존(q<=0.05): {k_star}/{mm}\n")

    # ---- winner/loser split (the load-bearing conditional prediction) ----
    print("=== 패자/승자 분리 (과거 h일 수익 부호) · 반전은 losers에서 강해야 ===")
    print(f"  {'cell':>10} {'h':>3} {'bucket':>16} {'pooled_corr':>12} "
          f"{'hi-attn xs%':>12} {'n':>6} {'n_hi':>5}")
    for sname in ("ALL", "small", "illiquid") + (("hi-retail",) if retail else ()):
        keep = subsets.get(sname)
        if not keep:
            continue
        for h in HORIZONS:
            _wic, pool, _ = build_panel(data, abn, keep, h)
            wl = winner_loser(pool)
            for bucket, (ic, himean, n, nhi) in wl.items():
                print(f"  {sname:>10} {h:>3} {bucket:>16} {ic:>+12.3f} "
                      f"{himean:>+12.3f} {n:>6} {nhi:>5}")
        print()

    print("판정선: 소형·저유동·고개인 셀에서 IC<0 & perm BH 생존 & IC_wf<0 유지 "
          "& losers 고어텐션 xs<0 => 조건부 반전 신호. 아니면 방향 NULL 종결.")
    return 0


def load_keyword_series_local(path):
    # thin wrapper so the import stays near the top per E402-free convention
    from app.ml.period_keyword_dataset import load_keyword_series
    return load_keyword_series(path)


if __name__ == "__main__":
    raise SystemExit(main())
