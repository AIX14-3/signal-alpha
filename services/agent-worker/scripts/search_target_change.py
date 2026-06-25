#!/usr/bin/env python
"""Target change: does search predict future VOLATILITY / VOLUME (not direction)? (Stage 9)

Every prior test targeted return DIRECTION and found ~0. The literature says
attention predicts the SECOND moment — volatility and trading volume (magnitude) —
not direction (Vlastakis & Markellos 2012: search explains ~50% of VIX variability;
Da/Engelberg/Gao). This swaps the target while keeping the same DataLab predictor.

Predictor (point-in-time, past-only): ABNORMAL search = rolling z-score of the
stock's weekly search level over a trailing window ("is attention unusually high
now?"). Two search series: stock-name query and the keyword-pool composite.

Targets at week t, horizon H (weeks), pooled over 3 stocks:
  fwd_vol_H     = annualized realized vol of daily returns over the NEXT H weeks
  fwd_abvol_H   = mean log daily volume next H weeks minus trailing-52w mean (abnormal volume)
  fwd_ret_H     = excess return next H weeks  (the DIRECTION control — expect ~0)
plus the CONCURRENT volatility (t-H, t] for reference.

A meaningfully positive corr for vol/volume while fwd_ret stays ~0 is the whole
point: change the target and the (coincident-leaning) attention signal becomes
usable for volatility/volume nowcasting even though it can't call direction.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.period_keyword_dataset import load_keyword_series  # noqa: E402
from scripts.aggregate_search_momentum import composite_index, name_index  # noqa: E402
from scripts.event_study_leadlag import pearson, week_index, zscore  # noqa: E402

HORIZONS = {1: "1주", 4: "1개월", 13: "1분기"}
ROLL = 26  # trailing weeks for abnormal-search baseline


def load_daily_ohlcv(path):
    """{ticker: {'weeks': sorted week list, 'ret': {wk:[logret..]}, 'logv': {wk:[logvol..]},
                 'close': {wk: last_close}}}."""
    by_ticker: dict[str, list[tuple[date, float, float]]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                d = date.fromisoformat(r["date"][:10])
                c = float(r["close"])
                v = float(r["volume"])
            except (ValueError, TypeError, KeyError):
                continue
            if c > 0 and v > 0:
                by_ticker.setdefault(r["ticker"], []).append((d, c, v))
    out = {}
    for t, rows in by_ticker.items():
        rows.sort()
        ret: dict[int, list[float]] = {}
        logv: dict[int, list[float]] = {}
        close: dict[int, float] = {}
        last_day: dict[int, date] = {}
        for i, (d, c, v) in enumerate(rows):
            w = week_index(d)
            logv.setdefault(w, []).append(math.log(v))
            if w not in last_day or d > last_day[w]:
                last_day[w] = d
                close[w] = c
            if i > 0 and rows[i - 1][1] > 0:
                ret.setdefault(w, []).append(math.log(c / rows[i - 1][1]))
        out[t] = {"ret": ret, "logv": logv, "close": close, "weeks": sorted(close)}
    return out


def _vol(ret_by_week, a, b):
    """Annualized std of daily log returns over weeks (a, b]."""
    vals = [r for w in range(a + 1, b + 1) for r in ret_by_week.get(w, [])]
    if len(vals) < 5:
        return None
    return statistics.pstdev(vals) * math.sqrt(252) * 100.0


def _mean_logv(logv_by_week, a, b):
    vals = [x for w in range(a + 1, b + 1) for x in logv_by_week.get(w, [])]
    return statistics.mean(vals) if vals else None


def _abnormal(level_by_week: dict[int, float]) -> dict[int, float]:
    """Rolling z-score of the search level over the trailing ROLL weeks (past-only)."""
    weeks = sorted(level_by_week)
    out: dict[int, float] = {}
    for w in weeks:
        hist = [level_by_week[x] for x in range(w - ROLL, w) if x in level_by_week]
        if len(hist) < ROLL // 2:
            continue
        mu = statistics.mean(hist)
        sd = statistics.pstdev(hist)
        if sd > 0:
            out[w] = (level_by_week[w] - mu) / sd
    return out


def analyze(label, level_fn, search_csv, ohlcv, tickers):
    print(f"\n{'='*74}\n[{label}]  ABNORMAL 검색 → 미래 변동성/거래량/방향 (3종목 pooled)\n{'='*74}")
    series_by_kw = load_keyword_series(search_csv)
    print(f"{'horizon':>8} {'n':>5} {'n_indep':>7} {'vol(미래)':>10} {'vol(동시)':>10} "
          f"{'vol증분':>9} {'volume(미래)':>12} {'return(방향)':>12}")
    for h in HORIZONS:
        ab, fvol, cvol, dvol, fvolu, fret = [], [], [], [], [], []
        for t in tickers:
            level = level_fn(series_by_kw, t)
            o = ohlcv.get(t)
            if not level or not o:
                continue
            abn = _abnormal(level)
            closes = o["close"]
            weeks = sorted(set(abn) & set(closes))
            for w in weeks:
                a = abn[w]
                fv = _vol(o["ret"], w, w + h)
                cv = _vol(o["ret"], w - h, w)
                base = _mean_logv(o["logv"], w - 52, w)
                fwd_lv = _mean_logv(o["logv"], w, w + h)
                c0, c1 = closes.get(w), closes.get(w + h)
                if None in (fv, cv, base, fwd_lv, c0, c1) or c0 == 0:
                    continue
                ab.append(a)
                fvol.append(fv)
                cvol.append(cv)
                dvol.append(fv - cv)  # vol INCREASE beyond current -> genuine lead vs persistence
                fvolu.append(fwd_lv - base)
                fret.append((c1 / c0 - 1.0) * 100.0)
        n_indep = len(ab) // h if h else len(ab)
        print(f"{HORIZONS[h]:>8} {len(ab):>5} {n_indep:>7} "
              f"{_f(pearson(ab, fvol)):>10} {_f(pearson(ab, cvol)):>10} "
              f"{_f(pearson(ab, dvol)):>9} {_f(pearson(ab, fvolu)):>12} {_f(pearson(ab, fret)):>12}")
    print("  Read: vol/volume corr이 방향(≈0)보다 크면 '타깃 바꾸면 신호 있음'. 단 'vol(미래)'엔 변동성 "
          "군집성이 섞임 → 'vol증분'(미래-동시)이 +면 현재 변동성을 넘어 *상승*을 선행(진짜 예측력), ≈0이면 "
          "대부분 군집성/동시.")


def _f(v):
    return f"{v:>+.3f}" if isinstance(v, float) else f"{'n/a':>6}"


def main() -> int:
    p = argparse.ArgumentParser(description="Search vs future volatility/volume (target change)")
    p.add_argument("--keyword-csv", default="datalab_patent_keywords.csv")
    p.add_argument("--stockname-csv", default="stockname_datalab.csv")
    p.add_argument("--ohlcv-csv", default="daily_ohlcv.csv")
    p.add_argument("--tickers", default="005930,000660,035420")
    args = p.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    ohlcv = load_daily_ohlcv(args.ohlcv_csv)
    analyze("stockname (종목명 검색)", _name_level, args.stockname_csv, ohlcv, tickers)
    analyze("composite (키워드풀 평균)", composite_index, args.keyword_csv, ohlcv, tickers)
    return 0


def _name_level(series_by_kw, ticker) -> dict[int, float]:
    """Stock-name weekly series, z-scored full period (comparable 'level')."""
    raw = name_index(series_by_kw, ticker)
    weeks = sorted(raw)
    zs = zscore([raw[w] for w in weeks])
    if zs is None:
        return {}
    return {w: z for w, z in zip(weeks, zs) if z is not None}


if __name__ == "__main__":
    raise SystemExit(main())
