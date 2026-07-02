#!/usr/bin/env python
"""SCRATCH — audit gap ③: signed per-stock keyword pairs (호재/악재) → direction?

Literature predicts null; we test it literally. Feature = pos-minus-neg search
(종목명 호재 − 종목명 악재). signed_ret = sign(signed) × forward return. Pooled corr +
hit-rate + within-day sign-shuffle permutation. NOTE: collection showed this channel is
DATA-SPARSE (only ~6 mega-caps have both 호재 & 악재 with >=20 nonzero days) — so this is
an underpowered confirmation, not a powered test.

    PYTHONIOENCODING=utf-8 uv run python scripts/scratch_signed_kw.py \
        --signed-csv signed_kw.csv --prices-csv prices_krx250.csv
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.event_study_leadlag import pearson  # noqa: E402
from scripts.search_to_magnitude import load_px  # noqa: E402

random.seed(42)
NPERM = 2000
HORIZONS = (1, 5)
MIN_DAYS = 15


def load_signed(path):
    pos, neg = defaultdict(dict), defaultdict(dict)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                v = float(r["ratio"])
            except (ValueError, KeyError):
                continue
            (pos if r["keyword"].endswith("호재") else neg)[r["ticker"]][r["period"]] = v
    return pos, neg


def fwd_ret(stk, d, h):
    td, cl, pos = stk["td"], stk["close"], stk["pos"]
    i = pos.get(d)
    if i is None or i + h >= len(td):
        return None
    c0, c1 = cl.get(td[i]), cl.get(td[i + h])
    return (c1 / c0 - 1.0) * 100.0 if c0 and c1 and c0 > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signed-csv", default="signed_kw.csv")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    a = ap.parse_args()

    pos, neg = load_signed(a.signed_csv)
    data = load_px(a.prices_csv)
    tickers = [t for t in set(pos) | set(neg) if t in data]
    usable = [t for t in tickers
              if sum(1 for v in pos.get(t, {}).values() if v > 0) >= MIN_DAYS
              and sum(1 for v in neg.get(t, {}).values() if v > 0) >= MIN_DAYS]
    print(f"종목(가격보유) {len(tickers)} · 호재&악재 둘다>={MIN_DAYS}일 {len(usable)}: {usable}\n")

    print("=== signed = 호재−악재 → 방향 (pooled, signed_ret=sign×fwd%) ===")
    print(f"  {'H(일)':>5} {'n':>6} {'corr(signed,ret)':>16} {'signed_ret%':>11} {'hit율':>6} {'perm_p':>7}")
    for h in HORIZONS:
        xs, ys, signs = [], [], []
        for t in usable:
            stk = data[t]
            days = sorted(set(pos.get(t, {})) | set(neg.get(t, {})))
            for d in days:
                sg = pos.get(t, {}).get(d, 0.0) - neg.get(t, {}).get(d, 0.0)
                if sg == 0:
                    continue
                fr = fwd_ret(stk, d, h)
                if fr is None:
                    continue
                xs.append(sg); ys.append(fr); signs.append(1.0 if sg > 0 else -1.0)
        n = len(xs)
        if n < 20:
            print(f"  {h:>5} {n:>6}  (too few)"); continue
        ic = pearson(xs, ys)
        signed_ret = [s * y for s, y in zip(signs, ys)]
        m = statistics.mean(signed_ret)
        hit = sum(1 for v in signed_ret if v > 0) / n
        # permutation: shuffle signs
        obs = m
        hits = 0
        sh = signs[:]
        for _ in range(NPERM):
            random.shuffle(sh)
            if abs(statistics.mean(s * y for s, y in zip(sh, ys))) >= abs(obs):
                hits += 1
        pp = (hits + 1) / (NPERM + 1)
        print(f"  {h:>5} {n:>6} {ic if ic is not None else float('nan'):>+16.3f} "
              f"{m:>+11.3f} {hit:>6.2f} {pp:>7.4f}")

    print("\n판정선: signed→방향 corr>0 & signed_ret>0 & perm_p<0.05 => 부호 키워드가 방향 보유. "
          "데이터 희소(6종목)로 애초에 검정력 부재 시 = 채널 미형성으로 종결.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
