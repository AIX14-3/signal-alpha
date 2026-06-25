"""합성 OHLCV 시드 — ML/DL 라인 MVP 입력 생성(실데이터 백필 전 단계).

`ohlcv_data` 가 비어 있으면 ML_INFER 가 모델을 못 돌리고 consensus-only 로 빠진다. 이 스크립트는
시드된 활성 종목에 **재현 가능한 합성 일봉**(GBM 경로)을 종목당 ~420 거래일 적재해, 변동성 모델
(EWMA/HAR-RV/GARCH/LightGBM)이 warmup(375)+lookback(400)을 채우고 돌 수 있게 한다. 멱등(upsert).

  uv run python seed_synthetic_ohlcv.py --tickers 005930,000660 --sessions 420 --seed 42
  uv run python seed_synthetic_ohlcv.py --limit 6        # 활성 종목 상위 6

합성 데이터임을 분명히 한다 — 가격 수준·변동성은 임의이며, 모델 파이프라인이 끝까지 도는지
검증하고 배치 베이스라인을 만드는 용도다(실데이터는 Kiwoom/DART 백필로 대체).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()


def _ticker_seed(base_seed: int, ticker: str) -> int:
    """종목별 안정·재현 시드(이름 기반) — 종목마다 다른 경로, 같은 ticker는 항상 동일."""
    digest = hashlib.sha256(f"{base_seed}:{ticker}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def synth_ohlcv(
    *,
    ticker: str,
    sessions: int,
    end: date,
    base_seed: int,
    start_price: float = 50_000.0,
    mu: float = 0.0002,
    sigma: float = 0.02,
) -> pd.DataFrame:
    """GBM 일봉 경로 — 거래일(월~금) 인덱스 + OHLCV. 결정론(시드 고정)."""
    rng = np.random.default_rng(_ticker_seed(base_seed, ticker))
    trade_dates = pd.bdate_range(end=pd.Timestamp(end), periods=sessions)

    # 종목별로 시작가를 흩뿌려(0.5x~2.5x) 베이스라인 표가 단조롭지 않게.
    p0 = start_price * float(rng.uniform(0.5, 2.5))
    rets = rng.normal(mu, sigma, size=sessions)
    close = p0 * np.exp(np.cumsum(rets))

    prev_close = np.concatenate([[close[0]], close[:-1]])
    open_ = prev_close * (1.0 + rng.normal(0.0, sigma * 0.3, size=sessions))
    intraday = np.abs(rng.normal(0.0, sigma, size=sessions))
    high = np.maximum(open_, close) * (1.0 + intraday)
    low = np.minimum(open_, close) * (1.0 - intraday)
    volume = rng.lognormal(mean=13.0, sigma=0.4, size=sessions)

    return pd.DataFrame(
        {
            "trade_date": trade_dates.date,
            "open": np.round(open_, 2),
            "high": np.round(high, 2),
            "low": np.round(low, 2),
            "close": np.round(close, 2),
            "volume": volume.astype("int64"),
        }
    )


async def seed_stock(pool, *, stock_id: int, ticker: str, frame: pd.DataFrame) -> int:
    from signal_alpha_data_access.repositories import MarketDataRepository

    async with pool.acquire() as conn:
        market = MarketDataRepository(conn)
        async with conn.transaction():
            for row in frame.itertuples(index=False):
                await market.upsert_ohlcv(
                    stock_id=stock_id,
                    trade_date=row.trade_date,
                    open_price=float(row.open),
                    high_price=float(row.high),
                    low_price=float(row.low),
                    close_price=float(row.close),
                    volume=int(row.volume),
                )
    return len(frame)


async def run(args: argparse.Namespace) -> None:
    load_env()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    end = date.fromisoformat(args.end) if args.end else date.today()

    pool = await build_pool(max_pool_size=4)
    try:
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=args.limit)
        if tickers:
            found = {t["ticker"] for t in targets}
            missing = [t for t in tickers if t not in found]
            if missing:
                print(f"⚠️  unknown tickers (skipped): {', '.join(missing)}")
        if not targets:
            raise SystemExit("No target stocks resolved (check --tickers / --limit / seeded stocks).")

        print("SYNTHETIC OHLCV SEED")
        print("=" * 64)
        print(f"sessions={args.sessions} end={end} seed={args.seed} targets={len(targets)}")
        total = 0
        for target in targets:
            frame = synth_ohlcv(
                ticker=target["ticker"], sessions=args.sessions, end=end, base_seed=args.seed
            )
            n = await seed_stock(
                pool, stock_id=int(target["stock_id"]), ticker=target["ticker"], frame=frame
            )
            total += n
            print(
                f"  {target['ticker']} {target['name']}: rows={n} "
                f"range={frame['trade_date'].iloc[0]}..{frame['trade_date'].iloc[-1]} "
                f"close={frame['close'].iloc[0]:.0f}->{frame['close'].iloc[-1]:.0f}"
            )
        print("-" * 64)
        print(f"SUMMARY upserted {total} rows across {len(targets)} stocks")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed reproducible synthetic OHLCV for the ML/DL MVP.")
    parser.add_argument("--tickers", help="Comma-separated tickers (e.g. 005930,000660).")
    parser.add_argument("--limit", type=int, default=6, help="If no --tickers, seed top-N active stocks.")
    parser.add_argument("--sessions", type=int, default=420, help="Trading sessions per stock (>400).")
    parser.add_argument("--end", help="Last trade date YYYY-MM-DD (default: today).")
    parser.add_argument("--seed", type=int, default=42, help="Base RNG seed (reproducible).")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
