"""Collect the KOSPI200 daily panel from KRX (pykrx) into local parquet.

Usage (from harness/):

    uv run python -m signal_alpha_harness.collect_panel --years 10
    uv run python -m signal_alpha_harness.collect_panel --out data/panel_kospi200.parquet

200종목 × 10년은 pykrx 호출 ~400회(30~40분)라 **종목별 샤드**(data/shards/{ticker}.parquet)로
저장하고, 샤드가 이미 있으면 건너뛴다 — 중단 후 재실행하면 이어서 수집된다.
전 종목 샤드가 모이면 단일 패널 parquet으로 합친다.

The harness stays file-based on purpose: the backtest runner must work without a
database so the loop can run anywhere. Loading the same rows into ``ohlcv_data``
for the live pipeline is a separate, later step (Phase 6).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from signal_alpha_harness.universe import DATA_DIR, load_universe

DEFAULT_OUT = DATA_DIR / "panel_kospi200.parquet"
DEFAULT_SHARD_DIR = DATA_DIR / "shards"
PANEL_COLUMNS = [
    "trade_date",
    "ticker",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "foreign_net",
    "institution_net",
]

_OHLCV_RENAME = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
# Net trading value (buy - sell, KRW) per investor group.
_FLOW_RENAME = {"외국인합계": "foreign_net", "기관합계": "institution_net"}


def fetch_one(ticker: str, start: str, end: str, pause_sec: float = 0.4) -> pd.DataFrame:
    """Daily OHLCV plus investor flows when KRX still serves them anonymously.

    pykrx 1.0.5x returns an empty frame for trading-value endpoints unless KRX
    login credentials are configured, so flows are best-effort: missing data
    lands as NaN columns. 수급 팩터는 결측 시 해당 팩터를 z-score에서 제외하는
    규칙으로 진행한다 (키움 ka10059 백필은 Phase 6).
    """
    from pykrx import stock as krx

    ohlcv = krx.get_market_ohlcv(start, end, ticker)
    time.sleep(pause_sec)
    flows = krx.get_market_trading_value_by_date(start, end, ticker)
    time.sleep(pause_sec)

    merged = ohlcv.rename(columns=_OHLCV_RENAME)[list(_OHLCV_RENAME.values())]
    flow_cols = [column for column in _FLOW_RENAME if column in flows.columns]
    if flow_cols:
        renamed = flows.rename(columns=_FLOW_RENAME)[[_FLOW_RENAME[c] for c in flow_cols]]
        merged = merged.join(renamed, how="left")
    for column in _FLOW_RENAME.values():
        if column not in merged.columns:
            merged[column] = pd.NA

    merged.index.name = "trade_date"
    merged = merged.reset_index()
    merged["trade_date"] = pd.to_datetime(merged["trade_date"]).dt.strftime("%Y-%m-%d")
    merged["ticker"] = ticker
    return merged


def collect_shards(
    start: str,
    end: str,
    shard_dir: Path = DEFAULT_SHARD_DIR,
    retries: int = 2,
    universe_path: Path | None = None,
) -> list[Path]:
    """종목별 샤드 수집 — 이미 존재하는 샤드는 스킵 (중단 재개)."""
    universe = load_universe(universe_path)
    shard_dir.mkdir(parents=True, exist_ok=True)
    shards: list[Path] = []
    skipped = 0
    for position, stock in enumerate(universe, start=1):
        shard = shard_dir / f"{stock.ticker}.parquet"
        if shard.exists():
            shards.append(shard)
            skipped += 1
            continue
        for attempt in range(retries + 1):
            try:
                frame = fetch_one(stock.ticker, start, end)
                break
            except Exception as error:  # network/KRX hiccups: retry then fail loudly
                if attempt == retries:
                    raise RuntimeError(f"{stock.ticker} {stock.name} 수집 실패: {error}") from error
                time.sleep(2.0 * (attempt + 1))
        frame["name"] = stock.name
        frame[PANEL_COLUMNS].to_parquet(shard, index=False)
        shards.append(shard)
        print(f"[{position:3d}/{len(universe)}] {stock.name} ({stock.ticker}): {len(frame)} rows")
    if skipped:
        print(f"skipped {skipped} existing shards (resume)")
    return shards


def merge_shards(shards: list[Path]) -> pd.DataFrame:
    frames = [pd.read_parquet(shard) for shard in shards]
    panel = pd.concat(frames, ignore_index=True)
    return panel[PANEL_COLUMNS].sort_values(["trade_date", "ticker"]).reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect the KOSPI200 panel via pykrx")
    parser.add_argument("--years", type=int, default=10, help="lookback window in years")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output parquet path")
    parser.add_argument("--shard-dir", type=Path, default=DEFAULT_SHARD_DIR)
    parser.add_argument("--universe", type=Path, default=None, help="universe snapshot CSV")
    args = parser.parse_args(argv)

    end = date.today()
    start = end - timedelta(days=args.years * 365)
    shards = collect_shards(
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
        shard_dir=args.shard_dir,
        universe_path=args.universe,
    )
    panel = merge_shards(shards)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(args.out, index=False)
    print(
        f"saved {len(panel)} rows x {panel['ticker'].nunique()} tickers "
        f"({panel['trade_date'].min()} ~ {panel['trade_date'].max()}) -> {args.out}"
    )
    flow_missing = panel["foreign_net"].isna().mean()
    if flow_missing > 0.5:
        print(
            f"warning: investor flows missing for {flow_missing:.0%} of rows "
            "(KRX login required in pykrx 1.0.5x) — 수급 팩터는 결측 제외 규칙으로 진행"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
