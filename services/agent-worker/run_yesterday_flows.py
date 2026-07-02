"""어제 일봉(ka10081) + 확정 수급(ka10059)을 Neon(ohlcv_data)에 적재하는 러너.

수집기 HTTP /internal/price/collect 는 today 고정이라 별도 러너로 어제 날짜를 처리한다.
1) ka10081 일봉으로 어제 OHLCV 행을 upsert(없는 행 생성).
2) 기존 run_investor_flow_update(ka10059)로 외국인/기관 순매수 채움(UPDATE 전용).
발급 키가 모의투자 키라 mock 도메인 사용(조회 TR은 실시세 중계)."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker
from mvp_runtime import bootstrap, build_pool, load_env

bootstrap()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yesterday_flows")

_UPSERT_OHLCV = """
INSERT INTO ohlcv_data (stock_id, trade_date, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (stock_id, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume
"""


def _bridge_kiwoom_env() -> None:
    """.env(KIWOOM_SECRET_KEY) → config(KIWOOM_APP_SECRET) 간극만 메모리에서 연결(시크릿 미출력).
    발급 키가 모의투자 키라 mock 도메인 사용."""
    import os

    if not os.environ.get("KIWOOM_APP_SECRET"):
        secret = os.environ.get("KIWOOM_SECRET_KEY", "")
        if secret:
            os.environ["KIWOOM_APP_SECRET"] = secret
    os.environ.setdefault("KIWOOM_API_BASE", "https://mockapi.kiwoom.com")
    # 모의 서버가 0.25s 간격에도 429를 던져 호출 간격을 넉넉히 둔다.
    os.environ["KIWOOM_MIN_REQUEST_INTERVAL_SEC"] = "1.5"


def _to_int(value: object) -> int:
    s = str(value or "").replace(",", "").strip().lstrip("+")
    if s.startswith("-"):
        s = s[1:]
    return int(s) if s else 0


async def main() -> None:
    load_env()
    _bridge_kiwoom_env()
    import httpx
    from app.core.config import get_settings
    from app.collectors.price.market_hours import now_kst
    from app.collectors.price.runner import build_client
    from app.collectors.price.pipeline import run_investor_flow_update
    from app.collectors.price.repository import PriceSnapshotRepository

    settings = get_settings()
    target_date = now_kst().date() - timedelta(days=1)  # 어제
    ymd = target_date.strftime("%Y%m%d")
    pool = await build_pool(max_pool_size=4)
    try:
        repo = PriceSnapshotRepository(pool)
        targets = await repo.list_target_stocks()
        logger.info(
            "api_base=%s date=%s targets=%d", settings.kiwoom_api_base, target_date, len(targets)
        )

        stored = skipped = failed = 0
        async with httpx.AsyncClient(timeout=settings.kiwoom_timeout_seconds) as http:
            client = build_client(settings, http)

            async def _chart_with_retry(ticker: str, tries: int = 4) -> dict:
                last: Exception | None = None
                for attempt in range(tries):
                    try:
                        return await client.request(
                            "ka10081",
                            {"stk_cd": ticker, "base_dt": ymd, "upd_stkpc_tp": "1"},
                            path="/api/dostk/chart",
                        )
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code != 429:
                            raise
                        last = exc
                        await asyncio.sleep(2.0 * (attempt + 1))
                raise last  # type: ignore[misc]

            # 1) ka10081 일봉 → ohlcv_data upsert (어제 행 생성)
            for t in targets:
                try:
                    payload = await _chart_with_retry(t.ticker)
                    rows = payload.get("stk_dt_pole_chart_qry") or []
                    row = next((r for r in rows if str(r.get("dt")) == ymd), None)
                    if row is None:
                        skipped += 1
                        logger.info("%s: no daily bar for %s", t.ticker, ymd)
                        continue
                    await pool.execute(
                        _UPSERT_OHLCV,
                        t.stock_id,
                        target_date,
                        _to_int(row["open_pric"]),
                        _to_int(row["high_pric"]),
                        _to_int(row["low_pric"]),
                        _to_int(row["cur_prc"]),
                        _to_int(row["trde_qty"]),
                    )
                    stored += 1
                except Exception as exc:  # noqa: BLE001 - 한 종목 실패가 전체를 막지 않음
                    failed += 1
                    logger.warning("ohlcv upsert failed for %s: %s", t.ticker, exc)
            print(f"[OHLCV] date={target_date} stored={stored} skipped={skipped} failed={failed}")

            # 2) ka10059 확정 수급 → 외국인/기관 순매수 채움(방금 만든 행 UPDATE)
            flow_stats = await run_investor_flow_update(client, repo, targets, target_date)
        print(
            f"[FLOWS] date={target_date} collected={flow_stats.collected} "
            f"stored={flow_stats.stored} skipped={flow_stats.skipped} failed={flow_stats.failed}"
        )
        if flow_stats.errors:
            print("flow errors:", flow_stats.errors[:10])
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
