#!/usr/bin/env python
"""SCRATCH (uncommitted) — FUSION angle: does a SIGNED DART event + search ATTENTION
recover a tradeable DIRECTION signal that search alone cannot?

Deep-research verdict: search alone never gives per-stock direction (magnitude only).
The one live path is fusion — the SIGN comes from an external directional source and
search GATES/AMPLIFIES it (Ranco et al. 2016). We already tested NEUTRAL DART event
keywords ("[종목] 이슈") → NULL; we have NOT tested SIGNED events. DART disclosure TYPES
carry a directional sign (유상증자·횡령·소송 = −, 공급계약·배당·자사주취득 = +). This:

  1. SIGN-ALONE  : do signed disclosure events predict next-day direction at all?
                   signed_ret = sign × market-adjusted fwd excess return [d+1, d+H].
                   mean, t, hit-rate, IC(sign, fwd_excess). (d+1 entry ⇒ no look-ahead;
                   DART filings are typically post-close.)
  2. FUSION      : does search ATTENTION at the event gate/amplify the signed move?
                   split events by PIT abnormal search tercile; is signed_ret larger in
                   the high-attention tercile? (Ranco: attention amplifies news reaction.)
  3. rigor       : within-date sign-shuffle permutation (NPERM), per-horizon, by event
                   type, era split.

Uses ONLY the already-collected dart_disclosures.json (research use, no DB / main-server).
Read-only, local files. No commits.

    PYTHONIOENCODING=utf-8 uv run python scripts/scratch_dart_sign_fusion.py \
        --disclosures dart_disclosures.json --prices-csv prices_krx250.csv \
        --search-csv stockname_daily_krx250.csv --benchmark-csv prices_kospi15_2016_2026.csv
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.period_keyword_dataset import load_keyword_series  # noqa: E402
from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.search_pead import fwd_excess  # noqa: E402
from scripts.search_to_magnitude import ffill, load_px, rolling_z, search_by_date  # noqa: E402

random.seed(42)
NPERM = 2000
HORIZONS = (1, 5, 20)   # trading days after d+1 entry
ABN_HI = 1.0
SPLIT = date(2021, 1, 1)

# Directional sign lexicon over DART disclosure titles. NEG checked before POS;
# a title matching both signs is dropped as ambiguous. (Counts from this corpus in
# comments.) 자기주식처분 = adds float ⇒ mildly negative; 자기주식취득(buyback) = positive.
NEG = ["유상증자", "감자", "자기주식처분", "횡령", "배임", "상장폐지", "관리종목",
       "거래정지", "불성실공시", "자본잠식", "부도", "채무불이행", "영업정지", "소송", "채무보증"]
POS = ["단일판매", "공급계약", "무상증자", "자기주식취득", "자기주식소각", "흑자전환",
       "신규시설", "시설투자", "배당", "기술이전", "특허취득"]


def sign_of(nm: str):
    neg = next((p for p in NEG if p in nm), None)
    pos = next((p for p in POS if p in nm), None)
    if neg and not pos:
        return -1, neg
    if pos and not neg:
        return 1, pos
    return 0, None


def parse_dt(s: str):
    s = s.strip()[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def signed_events(disclosures):
    """[(ticker, date, sign, event_type)] deduped per (ticker, date, sign, type)."""
    seen, out = set(), []
    for t, rows in disclosures.items():
        for r in rows:
            sgn, et = sign_of(r.get("report_nm", ""))
            if sgn == 0:
                continue
            d = parse_dt(r.get("rcept_dt", ""))
            if d is None:
                continue
            key = (t, d, sgn, et)
            if key in seen:
                continue
            seen.add(key)
            out.append((t, d, sgn, et))
    return out


def econ(vals):
    n = len(vals)
    if n < 8:
        return None
    m, sd = statistics.mean(vals), statistics.pstdev(vals)
    return {"n": n, "mean": m, "t": (m / (sd / n ** 0.5) if sd > 0 else float("nan")),
            "hit": sum(1 for v in vals if v > 0) / n}


def perm_p_mean(vals, signs):
    """Null: shuffle the SIGN across events (break sign↔return), recompute mean signed_ret.
    vals here are the raw fwd_excess; signed_ret = sign*excess."""
    obs = statistics.mean(s * v for s, v in zip(signs, vals))
    if len(vals) < 8:
        return float("nan")
    hits = 0
    sh = signs[:]
    for _ in range(NPERM):
        random.shuffle(sh)
        if abs(statistics.mean(s * v for s, v in zip(sh, vals))) >= abs(obs):
            hits += 1
    return (hits + 1) / (NPERM + 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosures", default="dart_disclosures.json")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    ap.add_argument("--search-csv", default="stockname_daily_krx250.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--skip", type=int, default=1)
    a = ap.parse_args()

    disclosures = json.loads(Path(a.disclosures).read_text(encoding="utf-8"))
    events = signed_events(disclosures)
    prices = load_prices_csv(a.prices_csv)
    bench = load_prices_csv(a.benchmark_csv).get(a.benchmark)
    if bench is None:
        print(f"benchmark {a.benchmark} 없음"); return 1

    # PIT abnormal search by date (rolling z)
    pxv = load_px(a.prices_csv)
    rbd = search_by_date(load_keyword_series(a.search_csv))
    abn = {t: rolling_z(stk["td"], ffill(stk["td"], rbd[t]))
           for t, stk in pxv.items() if t in rbd and rbd[t]}

    npos = sum(1 for e in events if e[2] > 0)
    nneg = len(events) - npos
    print(f"signed 이벤트 {len(events)}건 (+{npos} / -{nneg}) · {len(prices)}종목 가격 · "
          f"drift entry d+{a.skip} · NPERM={NPERM}\n")

    # build per-event records at each horizon
    print("=== 1) SIGN-ALONE: signed 이벤트가 방향을 예측하나 (signed_ret=sign×초과수익%) ===")
    print(f"  {'H(일)':>5} {'n':>5} {'signed_ret%':>11} {'t':>7} {'hit율':>6} "
          f"{'perm_p':>7} {'IC(sign,ret)':>12}")
    from scripts.event_study_leadlag import pearson  # noqa: E402
    recs_by_h = {}
    for h in HORIZONS:
        recs = []  # (ticker, date, sign, et, excess, attn)
        for t, d, sgn, et in events:
            if t not in prices:
                continue
            ex = fwd_excess(prices[t], bench, d, skip=a.skip, win=h)
            if ex is None:
                continue
            attn = abn.get(t, {}).get(d.isoformat())
            recs.append((t, d, sgn, et, ex, attn))
        recs_by_h[h] = recs
        signed = [s * ex for _, _, s, _, ex, _ in recs]
        st = econ(signed)
        pp = perm_p_mean([ex for *_, ex, _ in recs], [s for _, _, s, _, _, _ in recs])
        ic = pearson([float(s) for _, _, s, _, _, _ in recs], [ex for *_, ex, _ in recs])
        if st:
            print(f"  {h:>5} {st['n']:>5} {st['mean']:>+11.3f} {st['t']:>+7.2f} "
                  f"{st['hit']:>6.2f} {pp:>7.4f} {ic if ic is not None else float('nan'):>+12.3f}")

    # 2) FUSION — attention gating at H=5
    print("\n=== 2) FUSION: 검색 어텐션이 signed 반응을 증폭하나 (H=5, 어텐션 tercile) ===")
    recs = [r for r in recs_by_h[5] if r[5] is not None]
    print(f"  어텐션 관측된 이벤트 {len(recs)}/{len(recs_by_h[5])}")
    if len(recs) >= 30:
        attns = sorted(r[5] for r in recs)
        k = len(attns) // 3
        lo_t, hi_t = attns[k], attns[-k]
        buckets = {"low-attn": [], "mid": [], "high-attn": []}
        for _, _, s, _, ex, at in recs:
            b = "low-attn" if at <= lo_t else ("high-attn" if at >= hi_t else "mid")
            buckets[b].append(s * ex)
        print(f"  {'bucket':>10} {'n':>5} {'signed_ret%':>11} {'t':>7} {'hit율':>6}")
        for b in ("low-attn", "mid", "high-attn"):
            st = econ(buckets[b])
            if st:
                print(f"  {b:>10} {st['n']:>5} {st['mean']:>+11.3f} {st['t']:>+7.2f} {st['hit']:>6.2f}")
        # amplification test: high vs low signed_ret gap, permutation on attention labels
        hi, lo = buckets["high-attn"], buckets["low-attn"]
        if len(hi) >= 8 and len(lo) >= 8:
            gap = statistics.mean(hi) - statistics.mean(lo)
            pooled = hi + lo
            nhi = len(hi); hits = 0
            for _ in range(NPERM):
                random.shuffle(pooled)
                if statistics.mean(pooled[:nhi]) - statistics.mean(pooled[nhi:]) >= gap:
                    hits += 1
            print(f"  증폭(high−low) = {gap:+.3f}%  perm_p(1-side) = {(hits + 1) / (NPERM + 1):.4f}")

    # 3) by event type (H=5) and era split
    print("\n=== 3) 이벤트 유형별 signed_ret (H=5, n>=20만) ===")
    by_et = defaultdict(list)
    for _, _, s, et, ex, _ in recs_by_h[5]:
        by_et[et].append(s * ex)
    print(f"  {'event_type':>14} {'n':>5} {'signed_ret%':>11} {'t':>7} {'hit율':>6}")
    for et in sorted(by_et, key=lambda e: -len(by_et[e])):
        st = econ(by_et[et])
        if st and st["n"] >= 20:
            print(f"  {et:>14} {st['n']:>5} {st['mean']:>+11.3f} {st['t']:>+7.2f} {st['hit']:>6.2f}")

    print("\n=== 4) era 분할 (H=5, signed_ret) ===")
    for nm, sel in (("2016~2020", lambda d: d < SPLIT), ("2021~2023", lambda d: d >= SPLIT)):
        vals = [s * ex for _, d, s, _, ex, _ in recs_by_h[5] if sel(d)]
        st = econ(vals)
        if st:
            print(f"  {nm}: n={st['n']} signed_ret={st['mean']:+.3f}% t={st['t']:+.2f} hit={st['hit']:.2f}")

    print("\n판정선: SIGN-ALONE signed_ret>0 & perm_p<0.05 => 부호 이벤트가 방향 정보 보유. "
          "FUSION high-attn > low-attn & 증폭 perm_p<0.05 => 검색이 방향 신호를 게이팅(다음=뉴스감성으로 부호 확장).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
