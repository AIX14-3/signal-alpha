"""Collect ~3y daily OHLCV for KOSPI top-20 (market cap) from Toss /api/v1/candles.

Writes ONE combined CSV (date,ticker,open,high,low,close,volume) for build_dataset
--from-csv. Per-ticker failures are logged and skipped so a wrong/delisted code
does not abort the run.

Output: vol-benchmark/raw/kospi20_ohlcv.csv
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
OUT = Path(os.getenv("OHLCV_OUT", r"C:/Users/biop9/vol-benchmark/raw/kospi20_ohlcv.csv"))
YEARS = 3
SAFETY_PAGES = 12  # 200 bars/page; 3y ~= 4 pages

# KOSPI 시총 top-20 (2026-06 근사, 보통주). 코드 검증은 런타임에 자동.
TICKERS = [
    ("000660", "SK하이닉스"), ("005930", "삼성전자"), ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"), ("012450", "한화에어로스페이스"), ("005380", "현대차"),
    ("009150", "삼성전기"), ("034020", "두산에너빌리티"), ("000270", "기아"),
    ("032830", "삼성생명"), ("105560", "KB금융"), ("068270", "셀트리온"),
    ("035420", "NAVER"), ("012330", "현대모비스"), ("055550", "신한지주"),
    ("042660", "한화오션"), ("329180", "HD현대중공업"), ("028260", "삼성물산"),
    ("015760", "한국전력"), ("267260", "HD현대일렉트릭"),
]

for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _candles(resp: object) -> list:
    if isinstance(resp, dict):
        res = resp.get("result", resp)
        if isinstance(res, dict):
            for key in ("candles", "data", "items"):
                if isinstance(res.get(key), list):
                    return res[key]
        if isinstance(res, list):
            return res
    return resp if isinstance(resp, list) else []


def _ts(c: dict) -> str | None:
    for k in ("timestamp", "dateTime", "date", "time", "dt"):
        if c.get(k):
            return str(c[k])
    return None


def _num(v: object) -> str:
    return "" if v is None else str(v).strip()


async def collect_symbol(toss: TossClient, code: str, start: dt.date) -> list[dict]:
    by_date: dict[str, dict] = {}
    before: str | None = None
    for _ in range(SAFETY_PAGES):
        params = {"symbol": code, "interval": "1d", "count": 200}
        if before:
            params["before"] = before
        resp = await toss.get("/api/v1/candles", params)
        rows = _candles(resp)
        if not rows:
            break
        page_min_date = page_min_ts = None
        for c in rows:
            ts = _ts(c)
            if not ts:
                continue
            d = ts[:10]
            by_date[d] = {
                "date": d, "ticker": code,
                "open": _num(c.get("openPrice") or c.get("open")),
                "high": _num(c.get("highPrice") or c.get("high")),
                "low": _num(c.get("lowPrice") or c.get("low")),
                "close": _num(c.get("closePrice") or c.get("close")),
                "volume": _num(c.get("volume")),
            }
            if page_min_date is None or d < page_min_date:
                page_min_date, page_min_ts = d, ts
        if page_min_ts is None:
            break
        before = page_min_ts
        if page_min_date <= start.isoformat():
            break
    today = dt.date.today().isoformat()
    out = [r for r in by_date.values() if start.isoformat() <= r["date"] < today]
    out.sort(key=lambda r: r["date"])
    return out


async def main() -> None:
    base = os.getenv("TOSS_API_BASE", "https://openapi.tossinvest.com").rstrip("/")
    start = dt.date.today() - dt.timedelta(days=365 * YEARS + 5)
    print(f"OHLCV 20종목  window>={start}  out={OUT}")
    all_rows: list[dict] = []
    ok, fail = [], []
    async with httpx.AsyncClient(timeout=20) as http:
        toss = TossClient(http=http, api_base=base, token_manager=TossTokenManager(
            http=http, api_base=base, client_id=os.getenv("TOSS_CLIENT_ID", ""),
            client_secret=os.getenv("TOSS_CLIENT_SECRET", "")), min_request_interval_sec=0.3)
        for code, name in TICKERS:
            try:
                rows = await collect_symbol(toss, code, start)
                if not rows:
                    fail.append((code, name, "0 rows"))
                    print(f"  FAIL {code} {name}: 0 rows")
                    continue
                all_rows.extend(rows)
                ok.append((code, name, len(rows)))
                print(f"  OK   {code} {name}: {len(rows)} rows ({rows[0]['date']}..{rows[-1]['date']})")
            except TossApiError as e:
                fail.append((code, name, f"HTTP{e.status}"))
                print(f"  FAIL {code} {name}: HTTP{e.status} {e.body[:70]}")
            except Exception as e:
                fail.append((code, name, type(e).__name__))
                print(f"  FAIL {code} {name}: {type(e).__name__} {str(e)[:70]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "ticker", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nDONE  ok={len(ok)} fail={len(fail)}  total_rows={len(all_rows)} -> {OUT}")
    if fail:
        print("실패 종목:", ", ".join(f"{c}({n}:{why})" for c, n, why in fail))


if __name__ == "__main__":
    asyncio.run(main())
