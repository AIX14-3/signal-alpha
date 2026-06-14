"""Backfill ~10 years of daily candles per universe symbol into parquet.

Reuses the Toss client already validated in spikes/toss-feasibility
(loaded by file path so we don't pollute sys.path). The candles endpoint
caps `count` at 200, so we page backwards with `before` until we reach the
target start date or the data runs out.

Candle shape (from the spike's FINDINGS):
  {"result": [{timestamp, openPrice, highPrice, lowPrice, closePrice,
               volume, currency}, ...]}   # newest-first
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

from config import DATA_DIR, REPO_ROOT, get_settings
from universe import UNIVERSE, Instrument

_SPIKE_CLIENT = REPO_ROOT / "spikes" / "toss-feasibility" / "toss_client.py"


def _load_toss_client():
    spec = importlib.util.spec_from_file_location("toss_client", _SPIKE_CLIENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def ingest_symbol(client, inst: Instrument, start: date) -> pd.DataFrame:
    rows: dict[str, dict] = {}
    before: str | None = None
    for _ in range(40):  # 40 * 200 = 8000 bars cap (>30y) safety
        params = {"symbol": inst.symbol, "interval": "1d", "count": 200, "adjusted": "true"}
        if before:
            params["before"] = before
        resp = await client.get("/api/v1/candles", params)
        # Shape: {"result": {"candles": [...newest-first...], "nextBefore": "..."}}
        result = resp.get("result", {}) if isinstance(resp, dict) else {}
        candles = result.get("candles") or []
        if not candles:
            break
        for c in candles:
            ts = str(c.get("timestamp", ""))[:10]
            if not ts:
                continue
            rows[ts] = {
                "date": ts,
                "open": _to_float(c.get("openPrice")),
                "high": _to_float(c.get("highPrice")),
                "low": _to_float(c.get("lowPrice")),
                "close": _to_float(c.get("closePrice")),
                "volume": _to_float(c.get("volume")),
            }
        oldest = str(candles[-1].get("timestamp", ""))[:10]
        before = result.get("nextBefore")
        if not before or oldest <= start.isoformat():
            break
    df = pd.DataFrame(sorted(rows.values(), key=lambda r: r["date"]))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return df


async def ingest_all() -> dict[str, int]:
    settings = get_settings()
    toss = _load_toss_client()
    start = date.today() - timedelta(days=365 * settings.years + 5)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    async with httpx.AsyncClient(timeout=15) as http:
        tokens = toss.TossTokenManager(
            http=http,
            api_base=settings.toss_api_base,
            client_id=settings.toss_client_id,
            client_secret=settings.toss_client_secret,
        )
        client = toss.TossClient(
            http=http,
            api_base=settings.toss_api_base,
            token_manager=tokens,
            min_request_interval_sec=settings.toss_min_interval_sec,
        )
        for inst in UNIVERSE:
            try:
                df = await ingest_symbol(client, inst, start)
            except Exception as exc:  # noqa: BLE001 - keep going on one bad symbol
                print(f"  [FAIL] {inst.symbol}: {exc}")
                counts[inst.symbol] = 0
                continue
            out = DATA_DIR / f"{inst.symbol}.parquet"
            df.to_parquet(out, index=False)
            counts[inst.symbol] = len(df)
            span = f"{df['date'].min().date()}~{df['date'].max().date()}" if len(df) else "-"
            print(f"  [OK]   {inst.symbol:6} {len(df):5} bars  {span}")
    return counts


def load_ohlcv(symbol: str) -> pd.DataFrame:
    """Read a previously-ingested parquet, indexed by date ascending."""
    path = DATA_DIR / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음 — 먼저 --ingest 실행")
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    asyncio.run(ingest_all())
