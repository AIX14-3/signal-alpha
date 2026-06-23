"""Backfill historical daily OHLCV into a LOCAL dev DB via FinanceDataReader.

Backtest-only data acquisition. The production price path is the Kiwoom REST
collector (docs/.../09_DB설계.md deprecated pykrx for prod); this is a one-off,
no-credentials backfill for the cause-lift backtest, used ONLY against a local DB.

Safety: refuses to run unless DATABASE_URL points at localhost/127.0.0.1 so it can
never write to prod (supabase).

Run (from services/agent-worker):
    $env:DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    uv run --with finance-datareader python scripts/backfill_ohlcv_fdr.py 2024-01-01 2026-06-01
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))

import asyncpg
import FinanceDataReader as fdr

from signal_alpha_data_access.repositories.market_data import MarketDataRepository


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(
            f"refusing to run: DATABASE_URL host is not local ({db_url.split('@')[-1]}). "
            "This backfill must target a local dev DB only."
        )


async def main(start: str, end: str) -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL is required (set it to the LOCAL docker DB).")
    _require_local(db_url)

    conn = await asyncpg.connect(db_url)
    try:
        repo = MarketDataRepository(conn)
        stocks = await conn.fetch("SELECT id, ticker FROM stocks ORDER BY id")
        total = 0
        for s in stocks:
            ticker = str(s["ticker"]).strip()
            try:
                df = fdr.DataReader(ticker, start, end)
            except Exception as exc:  # noqa: BLE001 — report and continue
                print(f"  {ticker} (id={s['id']}): FETCH FAILED — {exc}")
                continue
            n = 0
            for idx, row in df.iterrows():
                await repo.upsert_ohlcv(
                    stock_id=int(s["id"]),
                    trade_date=idx.date(),
                    open_price=int(row["Open"]),
                    high_price=int(row["High"]),
                    low_price=int(row["Low"]),
                    close_price=int(row["Close"]),
                    volume=int(row["Volume"]),
                    change_pct=round(float(row["Change"]) * 100.0, 4),
                )
                n += 1
            total += n
            print(f"  {ticker} (id={s['id']}): {n} rows  [{df.index.min().date()} → {df.index.max().date()}]"
                  if n else f"  {ticker} (id={s['id']}): 0 rows")
        print(f"TOTAL upserted: {total}")
    finally:
        await conn.close()


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-01"
    asyncio.run(main(start, end))
