"""시장 지수 미니차트(코스피·코스닥·원/달러·VIX) — item 7.

Yahoo Finance 차트 API(무키·JSON)를 서버사이드에서 조회한다(브라우저 CORS 회피). 개별 지수
조회 실패 시 결정론 데모 시계열로 폴백해 UI 가 절대 비지 않게 한다(is_demo=true 로 표시).
"""

from __future__ import annotations

import datetime
import random
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


@router.get("/indices")
async def get_indices() -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=8.0) as client:
        for idx in _INDICES:
            try:
                bars = await _fetch_yahoo(client, idx["symbol"])
                if len(bars) < 2:
                    raise ValueError("insufficient")
                out.append(_summarize(idx, bars, is_demo=False))
            except Exception:
                out.append(_demo_index(idx))
    return {"indices": out}


async def _fetch_yahoo(client: httpx.AsyncClient, symbol: str) -> list[dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = await client.get(
        url, params={"interval": "1d", "range": "1mo"}, headers={"User-Agent": _UA}
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    return [
        {"time": int(t), "close": round(float(c), 2)}
        for t, c in zip(stamps, closes)
        if c is not None
    ]


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
