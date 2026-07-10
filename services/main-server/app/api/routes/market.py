"""시장 지수 미니차트(코스피·코스닥·원/달러·VIX) — item 7.

Yahoo Finance 차트 API(무키·JSON)를 서버사이드에서 조회한다(브라우저 CORS 회피). 개별 지수
조회 실패 시 결정론 데모 시계열로 폴백해 UI 가 절대 비지 않게 한다(is_demo=true 로 표시).
"""

from __future__ import annotations

import asyncio
import datetime
import random
import time
from typing import Any

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/market", tags=["market"])

_INDICES = [
    {"key": "kospi", "name": "코스피", "symbol": "^KS11", "base": 2600.0},
    {"key": "kosdaq", "name": "코스닥", "symbol": "^KQ11", "base": 850.0},
    {"key": "usdkrw", "name": "원/달러", "symbol": "KRW=X", "base": 1350.0},
    {"key": "vix", "name": "VIX", "symbol": "^VIX", "base": 16.0},
]
_UA = "Mozilla/5.0 (compatible; SignalAlpha/1.0)"

_DAILY = ("1d", "1mo")
_HOURLY = ("1h", "1mo")

# Yahoo 는 호출이 잦으면 429 를 준다. 실패할 때마다 합성 시계열로 떨어지면 실데이터가 있는데도
# 화면이 "예시"로 뒤덮인다. 짧게 캐시해 호출 수를 줄이고(프론트는 45초마다 폴링), 일시적 실패
# 때는 마지막 실측값을 계속 쓴다. 합성값은 한 번도 못 받아 온 지수에만 쓰는 최후 수단이다.
_CACHE_TTL_SEC = 60.0
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@router.get("/indices")
async def get_indices() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=8.0) as client:
        series = await asyncio.gather(*(_bars_for(client, idx) for idx in _INDICES))
    return {
        "indices": [
            _summarize(idx, bars, is_demo=False) if bars else _demo_index(idx)
            for idx, bars in zip(_INDICES, series)
        ]
    }


async def _bars_for(client: httpx.AsyncClient, idx: dict[str, Any]) -> list[dict[str, Any]] | None:
    symbol = idx["symbol"]
    cached = _cache.get(symbol)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SEC:
        return cached[1]
    try:
        bars = await _fetch_yahoo(client, symbol)
        if len(bars) < 2:
            raise ValueError("insufficient")
    except Exception:
        return cached[1] if cached else None
    _cache[symbol] = (time.monotonic(), bars)
    return bars


async def _fetch_yahoo(client: httpx.AsyncClient, symbol: str) -> list[dict[str, Any]]:
    stamps, closes = await _fetch_closes(client, symbol, *_DAILY)
    # Yahoo 는 멀쩡한 거래일의 일봉 종가를 이따금 비워 둔다(관측: 2026-07-09 의 ^KQ11·KRW=X).
    # 빈 봉을 그냥 버리면 "전일 대비"가 이틀 전 종가와 비교돼 등락률이 부풀려진다(코스닥 +5.66%
    # ← 실제 +3.9%). 끝쪽에 구멍이 났을 때만 시간봉으로 그날 마지막 체결가를 메운다.
    if any(c is None for c in closes[-2:]):
        closes = _fill_gaps(stamps, closes, await _fetch_closes(client, symbol, *_HOURLY))
    return [
        {"time": int(t), "close": round(float(c), 2)}
        for t, c in zip(stamps, closes)
        if c is not None
    ]


async def _fetch_closes(
    client: httpx.AsyncClient, symbol: str, interval: str, rng: str
) -> tuple[list[int], list[float | None]]:
    """(타임스탬프, 종가) — 종가에는 Yahoo 가 비워 둔 None 이 그대로 남는다."""
    resp = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": interval, "range": rng},
        headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    stamps = [int(t) for t in (result.get("timestamp") or [])]
    closes = list(result["indicators"]["quote"][0].get("close") or [])
    return stamps, closes[: len(stamps)]


def _fill_gaps(
    stamps: list[int],
    closes: list[float | None],
    hourly: tuple[list[int], list[float | None]],
) -> list[float | None]:
    """빈 일봉 종가를 같은 날 시간봉의 마지막 값으로 메운다."""
    last_of_day: dict[str, float] = {}
    for t, c in zip(*hourly):
        if c is not None:
            last_of_day[_utc_day(t)] = float(c)
    return [c if c is not None else last_of_day.get(_utc_day(t)) for t, c in zip(stamps, closes)]


def _utc_day(stamp: int) -> str:
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).date().isoformat()


def _summarize(idx: dict[str, Any], bars: list[dict[str, Any]], *, is_demo: bool) -> dict[str, Any]:
    last = bars[-1]["close"]
    prev = bars[-2]["close"]
    change = round(last - prev, 2)
    pct = round(change / prev * 100, 2) if prev else 0.0
    return {
        "key": idx["key"],
        "name": idx["name"],
        "last": last,
        "change": change,
        "change_pct": pct,
        "bars": bars,
        "is_demo": is_demo,
    }


def _demo_index(idx: dict[str, Any]) -> dict[str, Any]:
    rng = random.Random(sum(ord(c) for c in idx["key"]))
    now = datetime.datetime.now(datetime.timezone.utc)
    price = float(idx["base"])
    bars: list[dict[str, Any]] = []
    for i in range(22):
        price = max(1.0, price * (1 + rng.uniform(-0.01, 0.01)))
        bars.append(
            {"time": int((now - datetime.timedelta(days=21 - i)).timestamp()), "close": round(price, 2)}
        )
    return _summarize(idx, bars, is_demo=True)
