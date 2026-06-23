"""Collect ~3y DAILY program-trading for KOSPI-20 via Kiwoom ka90004
(종목별프로그램매매현황 — per-date market top-50) on mockapi.

No per-stock daily TR exists, so we loop one call per trading day and keep the
rows whose stk_cd is in our 20. Days a stock isn't in the top-50 are simply
missing (filled/excluded downstream).

Output: date,ticker,prog_buy_qty,prog_sell_qty,prog_net_qty,prog_net_amt
-> vol-benchmark/raw/kospi20_program.csv
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
from kiwoom_client import KiwoomRestClient, RateLimiter, TokenManager

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.getenv("PROG_OUT", r"C:/Users/biop9/vol-benchmark/raw/kospi20_program.csv"))
YEARS = 3
CODES = {"000660", "005930", "373220", "207940", "012450", "005380", "009150",
         "034020", "000270", "032830", "105560", "068270", "035420", "012330",
         "055550", "042660", "329180", "028260", "015760", "267260"}

for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _int(v: object) -> int | str:
    s = str(v or "").strip().replace(",", "").replace("+", "")
    try:
        return int(s)
    except ValueError:
        return ""


async def _req(client, body):
    for attempt in range(6):
        try:
            return await client.request("ka90004", body, path="/api/dostk/stkinfo")
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 429:
                await asyncio.sleep(2.0 + 2.0 * attempt)
                continue
            raise
    raise RuntimeError("429 retries exhausted")


async def main() -> None:
    use_mock = os.getenv("KIWOOM_USE_MOCK", "0").strip() in {"1", "true", "yes"}
    base = (os.getenv("KIWOOM_REST_BASE_URL", "").strip()
            or ("https://mockapi.kiwoom.com" if use_mock else "https://api.kiwoom.com")).rstrip("/")
    today = dt.date.today()
    start = today - dt.timedelta(days=365 * YEARS + 5)
    days = [start + dt.timedelta(days=i) for i in range((today - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    print(f"프로그램매매  base={base}  {start}..{today}  weekdays={len(days)}  out={OUT}")

    rows, hit, empty = [], 0, 0
    async with httpx.AsyncClient(timeout=20) as http:
        client = KiwoomRestClient(http=http, api_base=base,
            token_manager=TokenManager(http=http, api_base=base,
                app_key=os.getenv("KIWOOM_APP_KEY", ""), app_secret=os.getenv("KIWOOM_SECRET_KEY", "")),
            rate_limiter=RateLimiter(float(os.getenv("KIWOOM_TR_DELAY_SEC", "1.0"))))
        for i, d in enumerate(days, 1):
            ymd = d.strftime("%Y%m%d")
            try:
                r = await _req(client, {"dt": ymd, "amt_qty_tp": "1",
                                        "mrkt_tp": "P00101", "stex_tp": "1"})
            except Exception as e:
                print(f"  {ymd} ERR {type(e).__name__}: {str(e)[:80]}")
                continue
            lst = r.get("stk_prm_trde_prst") or []
            if not lst:
                empty += 1
            for row in lst:
                code = str(row.get("stk_cd", "")).strip().lstrip("A")
                if code in CODES:
                    buy, sell = _int(row.get("buy_cntr_qty")), _int(row.get("sel_cntr_qty"))
                    net = (buy - sell) if isinstance(buy, int) and isinstance(sell, int) else ""
                    rows.append({"date": d.isoformat(), "ticker": code,
                                 "prog_buy_qty": buy, "prog_sell_qty": sell,
                                 "prog_net_qty": net, "prog_net_amt": _int(row.get("netprps_prica"))})
                    hit += 1
            if i % 100 == 0:
                print(f"  {i}/{len(days)} (rows={hit} empty_days={empty})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "ticker", "prog_buy_qty", "prog_sell_qty",
                                           "prog_net_qty", "prog_net_amt"])
        w.writeheader()
        w.writerows(rows)
    per = {}
    for r in rows:
        per[r["ticker"]] = per.get(r["ticker"], 0) + 1
    print(f"\nDONE rows={len(rows)} empty_days={empty} tickers_covered={len(per)} -> {OUT}")
    print("종목별 일수:", dict(sorted(per.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    asyncio.run(main())
