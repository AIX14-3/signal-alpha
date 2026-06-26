"""OHLCV 과거 일봉 백필 — Kiwoom ka10081(주식일봉차트조회) → ohlcv_data.

수집 데몬(intraday)과 별개로, 대상 종목의 N년치 일봉을 한 번에 적재한다(L6 forward-return
라벨 등 백테스트용). 멱등 — ON CONFLICT(stock_id, trade_date) UPSERT.

base_dt 를 과거로 walk-back 하며 페이지네이션(한 호출 ~600행). 대상 종목은 stocks 테이블.

사용법:
    uv run python backfill_ohlcv.py --years 3
    uv run python backfill_ohlcv.py --ticker 005930 --start-date 2023-06-26
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).parent))

from app.collectors.price.kiwoom.auth import TokenManager  # noqa: E402
from app.collectors.price.kiwoom.parsing import parse_int, parse_price  # noqa: E402
from app.collectors.price.kiwoom.rest_client import KiwoomRestClient  # noqa: E402
from app.collectors.price.rate_limiter import RateLimiter  # noqa: E402
from app.core.config import get_settings  # noqa: E402

CHART_PATH = "/api/dostk/chart"
CHART_API_ID = "ka10081"
BACKFILL_MIN_INTERVAL_SEC = 0.7
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

UPSERT_SQL = """
INSERT INTO ohlcv_data (stock_id, trade_date, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (stock_id, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume
"""


def _env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if ROOT_ENV.exists():
        for line in ROOT_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _to_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def build_client(http: httpx.AsyncClient) -> KiwoomRestClient:
    settings = get_settings()
    # NOTE: .env 는 KIWOOM_SECRET_KEY 를 쓰지만 config 는 KIWOOM_APP_SECRET 을 읽는다(불일치).
    # 백필은 두 이름 모두 허용해 우회한다(데몬 버그는 별도 수정).
    app_key = _env("KIWOOM_APP_KEY") or settings.kiwoom_app_key
    app_secret = _env("KIWOOM_SECRET_KEY") or _env("KIWOOM_APP_SECRET") or settings.kiwoom_app_secret
    tokens = TokenManager(
        http=http,
        api_base=settings.kiwoom_api_base,
        app_key=app_key or "",
        app_secret=app_secret or "",
    )
    # 백필은 버스트가 크므로 데몬 기본(0.25s)보다 넉넉히 띄운다(429 방어).
    interval = max(settings.kiwoom_min_request_interval_sec, BACKFILL_MIN_INTERVAL_SEC)
    limiter = RateLimiter(interval)
    return KiwoomRestClient(
        http=http, api_base=settings.kiwoom_api_base, token_manager=tokens, rate_limiter=limiter
    )


async def _request_with_retry(client: KiwoomRestClient, body: dict, *, max_retries: int = 5) -> dict:
    """429/일시 오류를 지수 백오프로 재시도."""
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            return await client.request(CHART_API_ID, body, path=CHART_PATH)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise


async def backfill_ticker(
    conn: asyncpg.Connection,
    client: KiwoomRestClient,
    *,
    stock_id: int,
    ticker: str,
    start: date,
    end: date,
) -> int:
    """ticker 의 [start, end] 일봉을 ka10081 로 페이지네이션 백필. 적재 행수 반환."""
    base_dt = end
    written = 0
    prev_oldest: date | None = None
    while True:
        payload = await _request_with_retry(
            client,
            {"stk_cd": ticker, "base_dt": base_dt.strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
        )
        rows = payload.get("stk_dt_pole_chart_qry") or []
        if not rows:
            break

        batch: list[tuple] = []
        oldest = base_dt
        for row in rows:
            d = _to_date(str(row["dt"]))
            oldest = min(oldest, d)
            if d < start or d > end:
                continue
            batch.append(
                (
                    stock_id,
                    d,
                    parse_price(row.get("open_pric")),
                    parse_price(row.get("high_pric")),
                    parse_price(row.get("low_pric")),
                    parse_price(row.get("cur_prc")),
                    parse_int(row.get("trde_qty")),
                )
            )
        if batch:
            await conn.executemany(UPSERT_SQL, batch)
            written += len(batch)

        if oldest <= start or prev_oldest == oldest:
            break  # 시작일 도달 또는 진전 없음(무한루프 방지)
        prev_oldest = oldest
        base_dt = oldest - timedelta(days=1)
    return written


async def run(args: argparse.Namespace) -> int:
    dsn = _env("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL 이 필요합니다.")
    end = date.today()
    start = _parse_start(args, end)

    conn = await asyncpg.connect(dsn)
    try:
        if args.ticker:
            rows = await conn.fetch("SELECT id, ticker FROM stocks WHERE ticker = $1", args.ticker)
        else:
            rows = await conn.fetch("SELECT id, ticker FROM stocks ORDER BY ticker")
        targets = [(int(r["id"]), str(r["ticker"])) for r in rows]
        print(f"백필 대상 {len(targets)}종목, 기간 {start} ~ {end}")

        async with httpx.AsyncClient(timeout=30) as http:
            client = build_client(http)
            total = 0
            for stock_id, ticker in targets:
                try:
                    n = await backfill_ticker(
                        conn, client, stock_id=stock_id, ticker=ticker, start=start, end=end
                    )
                    total += n
                    print(f"  [{ticker}] {n}행")
                except Exception as exc:  # noqa: BLE001 — 한 종목 실패가 전체를 막지 않게
                    print(f"  [{ticker}] 실패: {type(exc).__name__}: {str(exc)[:120]}")
        print(f"완료. 총 {total}행 적재.")
    finally:
        await conn.close()
    return 0


def _parse_start(args: argparse.Namespace, end: date) -> date:
    if args.start_date:
        return date.fromisoformat(args.start_date)
    return end - timedelta(days=int(args.years) * 365)


def main() -> None:
    parser = argparse.ArgumentParser(description="OHLCV 과거 일봉 백필 (Kiwoom ka10081)")
    parser.add_argument("--years", type=int, default=3, help="백필 연수 (기본 3)")
    parser.add_argument("--start-date", help="YYYY-MM-DD (지정 시 --years 무시)")
    parser.add_argument("--ticker", help="단일 종목만")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
