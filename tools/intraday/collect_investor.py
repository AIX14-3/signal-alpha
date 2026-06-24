"""Collect ~3y DAILY investor net-buy (foreign/inst/individual) per KOSPI-20 stock
via Kiwoom ka10059 (종목별투자자기관별) on mockapi (real market data).

One call returns ~100 trading days (<= dt, descending). We page backwards by
setting dt to the day before each page's oldest row until we pass the 3y start.

amt_qty_tp=1 (수량), trde_tp=0 (순매수). Output combined CSV:
    date, ticker, foreign_net, inst_net, indiv_net   (net-buy quantities)
-> vol-benchmark/raw/kospi20_investor.csv
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
from kiwoom_client import KiwoomRestClient, RateLimiter, TokenManager, KiwoomRestError

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.getenv("INV_OUT", r"C:/Users/biop9/vol-benchmark/raw/kospi20_investor.csv"))
YEARS = 3
MAX_PAGES = 15
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


def _int(v: object) -> str:
    s = str(v or "").strip().replace(",", "").replace("+", "")
    return s if s and s.lstrip("-").isdigit() else ""


async def _req(client: KiwoomRestClient, body: dict) -> dict:
    for attempt in range(6):
        try:
            return await client.request("ka10059", body, path="/api/dostk/stkinfo")
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 429:
                await asyncio.sleep(2.0 + 2.0 * attempt)  # backoff on rate limit
                continue
            raise
    raise RuntimeError("429 retries exhausted")


async def collect_symbol(client: KiwoomRestClient, code: str, start: str) -> list[dict]:
    by_date: dict[str, dict] = {}
    cur_dt = dt.date.today().strftime("%Y%m%d")
    for _ in range(MAX_PAGES):
        r = await _req(client, {
            "dt": cur_dt, "stk_cd": code,
            "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"})
        rows = r.get("stk_invsr_orgn") or []
        if not rows:
            break
        page_dates = []
        for row in rows:
            d = str(row.get("dt", "")).strip()
            if not d:
                continue
            page_dates.append(d)
            by_date[d] = {
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "ticker": code,
                "foreign_net": _int(row.get("frgnr_invsr")),
                "inst_net": _int(row.get("orgn")),
                "indiv_net": _int(row.get("ind_invsr")),
            }
        oldest = min(page_dates)
        if oldest <= start:
            break
        prev = (dt.datetime.strptime(oldest, "%Y%m%d").date() - dt.timedelta(days=1))
        cur_dt = prev.strftime("%Y%m%d")
    out = [v for k, v in by_date.items() if k >= start]
    out.sort(key=lambda r: r["date"])
    return out


async def main() -> None:
    use_mock = os.getenv("KIWOOM_USE_MOCK", "0").strip() in {"1", "true", "yes"}
    base = (os.getenv("KIWOOM_REST_BASE_URL", "").strip()
            or ("https://mockapi.kiwoom.com" if use_mock else "https://api.kiwoom.com")).rstrip("/")
    start = (dt.date.today() - dt.timedelta(days=365 * YEARS + 5)).strftime("%Y%m%d")
    print(f"투자자 수급  kiwoom_base={base}  start>={start}  out={OUT}")
    all_rows, ok, fail = [], [], []
    async with httpx.AsyncClient(timeout=20) as http:
        client = KiwoomRestClient(http=http, api_base=base,
            token_manager=TokenManager(http=http, api_base=base,
                app_key=os.getenv("KIWOOM_APP_KEY", ""), app_secret=os.getenv("KIWOOM_SECRET_KEY", "")),
            rate_limiter=RateLimiter(float(os.getenv("KIWOOM_TR_DELAY_SEC", "0.4"))))
        for code, name in TICKERS:
            try:
                rows = await collect_symbol(client, code, start)
                if not rows:
                    fail.append((code, name, "0 rows"))
                    print(f"  FAIL {code} {name}: 0 rows")
                    continue
                all_rows.extend(rows)
                ok.append((code, name, len(rows)))
                print(f"  OK   {code} {name}: {len(rows)} rows ({rows[0]['date']}..{rows[-1]['date']})")
            except KiwoomRestError as e:
                fail.append((code, name, str(e)[:50]))
                print(f"  FAIL {code} {name}: {e}")
            except Exception as e:
                fail.append((code, name, type(e).__name__))
                print(f"  FAIL {code} {name}: {type(e).__name__} {str(e)[:80]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "ticker", "foreign_net", "inst_net", "indiv_net"])
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nDONE ok={len(ok)} fail={len(fail)} total_rows={len(all_rows)} -> {OUT}")
    if fail:
        print("실패:", ", ".join(f"{c}({n}:{w})" for c, n, w in fail))


if __name__ == "__main__":
    asyncio.run(main())
