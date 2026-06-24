"""Collect ~3 years of DAILY USD->KRW exchange rate from Toss /api/v1/exchange-rate.

The exchange-rate endpoint is a point query (1-min reference rate) but supports a
historical `dateTime`, so we sample one value per weekday (12:00 KST). Holidays
just repeat the last valid rate or error -> skipped; the benchmark left-joins by
date with shift>=1 downstream.

Output CSV: date, usdkrw_rate, usdkrw_mid   (-> vol-benchmark/raw/fx_usdkrw.csv)
"""
from __future__ import annotations
import asyncio
import csv
import os
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from toss_client import TossClient, TossTokenManager, TossApiError

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.getenv("FX_OUT", r"C:/Users/biop9/vol-benchmark/raw/fx_usdkrw.csv"))
YEARS = 3
KST = dt.timezone(dt.timedelta(hours=9))

for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def main() -> None:
    base = os.getenv("TOSS_API_BASE", "https://openapi.tossinvest.com").rstrip("/")
    today = dt.datetime.now(KST).date()
    start = today - dt.timedelta(days=365 * YEARS + 5)
    days = [start + dt.timedelta(days=i) for i in range((today - start).days)]
    days = [d for d in days if d.weekday() < 5]  # weekdays only
    print(f"FX USD->KRW  {start}..{today}  weekdays={len(days)}  out={OUT}")

    rows, ok, fail = [], 0, 0
    async with httpx.AsyncClient(timeout=20) as http:
        toss = TossClient(http=http, api_base=base, token_manager=TossTokenManager(
            http=http, api_base=base, client_id=os.getenv("TOSS_CLIENT_ID", ""),
            client_secret=os.getenv("TOSS_CLIENT_SECRET", "")),
            min_request_interval_sec=0.3)
        for i, d in enumerate(days, 1):
            iso = f"{d.isoformat()}T12:00:00+09:00"
            try:
                r = await toss.get("/api/v1/exchange-rate",
                                   {"baseCurrency": "USD", "quoteCurrency": "KRW", "dateTime": iso})
                res = r.get("result", {})
                rows.append({"date": d.isoformat(),
                             "usdkrw_rate": res.get("rate", ""),
                             "usdkrw_mid": res.get("midRate", "")})
                ok += 1
            except TossApiError as e:
                fail += 1
                if e.status not in (404,):
                    print(f"  {d} HTTP{e.status}: {e.body[:80]}")
            except Exception as e:
                fail += 1
                print(f"  {d} ERR {type(e).__name__}: {str(e)[:80]}")
            if i % 100 == 0:
                print(f"  {i}/{len(days)} (ok={ok} fail={fail})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "usdkrw_rate", "usdkrw_mid"])
        w.writeheader()
        w.writerows(rows)
    span = f"{rows[0]['date']}..{rows[-1]['date']}" if rows else "EMPTY"
    print(f"DONE ok={ok} fail={fail} -> {OUT} ({len(rows)} rows, {span})")


if __name__ == "__main__":
    asyncio.run(main())
