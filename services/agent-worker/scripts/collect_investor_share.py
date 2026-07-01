#!/usr/bin/env python
"""Collect per-stock RETAIL participation (개인 거래비중) for the conditional-reversal
experiment (audit gap ①). Retail attention effects concentrate in the retail habitat
(small / illiquid / high-retail-ownership) — this sources the retail axis.

Primary source: KRX investor-trading value via pykrx. KRX's data portal now requires a
(free) member login; pykrx reads it from KRX_ID / KRX_PW env vars. This env has no KRX
creds by default, so:
  - `--smoke`  : call ONE ticker, print the raw pykrx schema so we lock the column names
                 (net vs gross, 개인/전체 labels) BEFORE the full pull. Prints which source
                 actually answered — numbers, not a GREEN.
  - full run   : per-ticker gross participation  개인(매도+매수) / Σ_all(매도+매수),
                 aggregated per MONTH (retail share is slow-moving; monthly is plenty and
                 keeps calls at ~1/ticker). Resumable: skips tickers already in the output.

Fallbacks if KRX stays blocked even with creds (documented, NOT silent):
  - fallback B (turnover proxy): pure-local, always works — see collect via --proxy turnover
    using prices csv + marcap json. Emitted with source='turnover_proxy'.

Output CSV columns: ticker,period(YYYY-MM),retail_frac,source   (uncommitted, research).

    # after adding KRX_ID/KRX_PW to signal-alpha/.env:
    PYTHONIOENCODING=utf-8 uv run --with pykrx --with python-dotenv \
        python scripts/collect_investor_share.py --tickers 005930 --smoke
    PYTHONIOENCODING=utf-8 uv run --with pykrx --with python-dotenv \
        python scripts/collect_investor_share.py --universe kosdaq_smallcap.json \
        --out retail_share_kosdaq.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_ENV = Path("C:/Users/804/Documents/GitHub/signal-alpha/.env")
START, END = "20160101", "20231231"


def load_env(env_path: Path) -> None:
    """Push KRX_ID / KRX_PW from the repo-root .env into os.environ (pykrx reads env)."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        print("  (python-dotenv 미설치 — --with python-dotenv 로 실행하세요)")
        return
    if not env_path.exists():
        print(f"  (.env 없음: {env_path})")
        return
    vals = dotenv_values(env_path)
    for k in ("KRX_ID", "KRX_PW"):
        if vals.get(k) and not os.environ.get(k):
            os.environ[k] = vals[k]
    have = [k for k in ("KRX_ID", "KRX_PW") if os.environ.get(k)]
    print(f"  KRX 자격증명 로드: {have or '없음'}")


def tickers_from_universe(path: str) -> list[str]:
    rows = json.load(open(path, encoding="utf-8"))
    return [r["ticker"] for r in rows]


def _find_cols(df):
    """Locate the 개인 column and a total/all-investor basis in a pykrx frame."""
    cols = [str(c) for c in df.columns]
    retail = next((c for c in cols if "개인" in c), None)
    total = next((c for c in cols if c in ("전체", "전체합계", "합계")), None)
    return retail, total, cols


def smoke(ticker: str, env_path: Path) -> int:
    """Print the raw schema of every candidate pykrx endpoint for one ticker/month."""
    load_env(env_path)
    try:
        from pykrx import stock
    except ImportError:
        print("pykrx 미설치 — `--with pykrx` 로 실행하세요.")
        return 1
    fm, to = "20230102", "20230131"
    print(f"\n[SMOKE] ticker={ticker}  {fm}~{to}")
    for name, fn in [
        ("get_market_trading_value_by_date",
         lambda: stock.get_market_trading_value_by_date(fm, to, ticker)),
        ("get_market_trading_value_by_investor",
         lambda: stock.get_market_trading_value_by_investor(fm, to, ticker)),
    ]:
        try:
            df = fn()
            print(f"\n  ✅ {name}: shape={df.shape}")
            print(f"     cols={list(df.columns)}")
            print(df.head(4).to_string())
            r, t, _ = _find_cols(df)
            print(f"     → 개인컬럼={r!r} 전체컬럼={t!r}")
        except Exception as e:  # noqa: BLE001 — smoke wants the raw failure text
            print(f"\n  ❌ {name}: {type(e).__name__}: {e}")
    print("\n  판정: 위에서 데이터가 실제로 찍힌 함수 = 사용할 소스. "
          "개인/전체 컬럼명 확인 후 collect()에 반영.")
    return 0


def monthly_retail_frac(df_by_date, retail_col, basis_cols):
    """Aggregate a by-date investor-value frame to {YYYY-MM: retail_frac} (gross share)."""
    num = defaultdict(float)  # 개인 gross
    den = defaultdict(float)  # all-investor gross
    for idx, row in df_by_date.iterrows():
        month = str(idx)[:7].replace("/", "-")
        try:
            num[month] += abs(float(row[retail_col]))
            den[month] += sum(abs(float(row[c])) for c in basis_cols)
        except (ValueError, TypeError, KeyError):
            continue
    return {m: num[m] / den[m] for m in num if den[m] > 0}


def collect(tickers, out_path, env_path: Path) -> int:
    load_env(env_path)
    try:
        from pykrx import stock
    except ImportError:
        print("pykrx 미설치 — `--with pykrx` 로 실행하세요.")
        return 1

    done = set()
    if Path(out_path).exists():
        with open(out_path, encoding="utf-8") as fh:
            done = {r["ticker"] for r in csv.DictReader(fh)}
    print(f"  대상 {len(tickers)}종목, 이미수집 {len(done)}, 남은 {len(tickers) - len(done)}")

    new = not Path(out_path).exists()
    ok = fail = 0
    with open(out_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ticker", "period", "retail_frac", "source"])
        for i, tk in enumerate(tickers):
            if tk in done:
                continue
            try:
                df = stock.get_market_trading_value_by_date(START, END, tk)
                retail, total, cols = _find_cols(df)
                if retail is None:
                    raise ValueError(f"개인 컬럼 없음: {cols}")
                # gross basis = all investor-type value columns except a total column
                basis = [c for c in cols if c != total] if total else cols
                frac = monthly_retail_frac(df, retail, basis)
                for period, v in sorted(frac.items()):
                    w.writerow([tk, period, f"{v:.5f}", "krx_investor"])
                fh.flush()
                ok += 1
            except Exception as e:  # noqa: BLE001
                fail += 1
                if fail <= 5:
                    print(f"    ❌ {tk}: {type(e).__name__}: {e}")
            if (i + 1) % 25 == 0:
                print(f"    ...{i + 1}/{len(tickers)}  ok={ok} fail={fail}")
    print(f"  완료: ok={ok} fail={fail} → {out_path}")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default="", help="쉼표구분 (스모크/부분수집)")
    p.add_argument("--universe", default="", help="티커 목록 json (kosdaq_smallcap.json 등)")
    p.add_argument("--out", default="retail_share.csv")
    p.add_argument("--env", default=str(DEFAULT_ENV))
    p.add_argument("--smoke", action="store_true")
    a = p.parse_args()

    env_path = Path(a.env)
    if a.smoke:
        tk = (a.tickers.split(",")[0].strip() if a.tickers else "005930")
        return smoke(tk, env_path)

    tickers = []
    if a.universe:
        tickers = tickers_from_universe(a.universe)
    if a.tickers:
        tickers += [t.strip() for t in a.tickers.split(",") if t.strip()]
    tickers = list(dict.fromkeys(tickers))  # dedup, keep order
    if not tickers:
        print("--universe 또는 --tickers 필요")
        return 1
    return collect(tickers, a.out, env_path)


if __name__ == "__main__":
    raise SystemExit(main())
