"""시장 지수·종목 시세가 Yahoo 응답의 함정 두 개에 걸리지 않는지.

둘 다 실제로 관측된 응답을 재현한 것이다(2026-07-10).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.api.routes import market, reports


def _chart(stamps, closes, *, ohlc=False, market_price=None):
    quote = {"close": closes}
    if ohlc:
        quote |= {"open": closes, "high": closes, "low": closes}
    return {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {"quote": [quote]},
                    "meta": {"regularMarketPrice": market_price},
                }
            ]
        }
    }


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    """(symbol, interval) → payload. 등록되지 않은 심볼은 Yahoo 의 404 처럼 예외."""

    def __init__(self, by_symbol):
        self._by_symbol = by_symbol
        self.calls: list[tuple[str, str]] = []

    async def get(self, url, params=None, headers=None):
        symbol = url.rsplit("/", 1)[-1]
        interval = (params or {}).get("interval", "")
        self.calls.append((symbol, interval))
        try:
            payload = self._by_symbol[(symbol, interval)]
        except KeyError as exc:
            raise RuntimeError(f"no such symbol: {symbol} {interval}") from exc
        return _Resp(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


_STAMPS = [1_783_468_800, 1_783_555_200, 1_783_641_600]  # 2026-07-08 / 07-09 / 07-10 (UTC)
_KOSDAQ_IDX = market._INDICES[1]


class MarketIndexTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        market._cache.clear()

    tearDown = setUp

    async def test_change_uses_hourly_when_daily_close_is_missing(self):
        """일봉 07-09 가 비어도 등락은 07-10 vs 07-09(시간봉) — 07-08 과 비교하면 부풀려진다."""
        client = _Client(
            {
                ("^KQ11", "1d"): _chart(_STAMPS, [785.0, None, 827.58]),
                ("^KQ11", "1h"): _chart([_STAMPS[1] + 3600, _STAMPS[2] + 3600], [796.54, 827.07]),
            }
        )

        bars = await market._fetch_yahoo(client, "^KQ11")

        self.assertEqual([b["close"] for b in bars], [785.0, 796.54, 827.58])
        summary = market._summarize(_KOSDAQ_IDX, bars, is_demo=False)
        self.assertAlmostEqual(summary["change_pct"], 3.9, delta=0.05)  # 이틀치 +5.42% 가 아니다

    async def test_skips_hourly_when_daily_tail_is_intact(self):
        client = _Client({("^KQ11", "1d"): _chart(_STAMPS, [785.0, 796.54, 827.58])})

        bars = await market._fetch_yahoo(client, "^KQ11")

        self.assertEqual(len(bars), 3)
        self.assertEqual([interval for _, interval in client.calls], ["1d"])

    async def test_serves_last_good_bars_when_yahoo_fails(self):
        """Yahoo 가 죽어도 한 번이라도 받아 둔 실측값이 있으면 합성값으로 떨어지지 않는다."""
        idx = market._INDICES[0]
        good = _Client({(idx["symbol"], "1d"): _chart(_STAMPS, [1.0, 2.0, 3.0])})
        expected = await market._bars_for(good, idx)
        self.assertTrue(expected)

        stamp, bars = market._cache[idx["symbol"]]
        market._cache[idx["symbol"]] = (stamp - 999, bars)  # TTL 만료시킨다

        self.assertEqual(await market._bars_for(_Client({}), idx), expected)

    async def test_falls_back_to_demo_only_when_never_fetched(self):
        self.assertIsNone(await market._bars_for(_Client({}), market._INDICES[0]))


class StockPriceSuffixTest(unittest.IsolatedAsyncioTestCase):
    def test_quote_agrees_rejects_wrong_suffix(self):
        # 247540.KS(유령): 현재가 19.4만원인데 마지막 종가는 11.15만원 → 다른 종목이다.
        self.assertFalse(reports._quote_agrees(194_000.0, 111_500))
        # 247540.KQ(진짜): 현재가 = 당일 종가.
        self.assertTrue(reports._quote_agrees(121_500.0, 121_500))
        self.assertFalse(reports._quote_agrees(None, 121_500))

    async def test_kosdaq_ignores_phantom_ks_symbol(self):
        """`.KS` 가 200 을 주더라도 코스닥 종목은 `.KQ` 의 오늘 시세를 써야 한다."""
        client = _Client(
            {
                ("247540.KS", "1d"): _chart(
                    _STAMPS, [112_600, 111_500, None], ohlc=True, market_price=194_000
                ),
                ("247540.KQ", "1d"): _chart(
                    _STAMPS, [112_600, 111_500, 121_500], ohlc=True, market_price=121_500
                ),
            }
        )

        with patch.object(reports.httpx, "AsyncClient", lambda **_: client):
            bars = await reports._yahoo_prices("247540", "day")

        self.assertIsNotNone(bars)
        self.assertEqual(bars[-1]["time"], "2026-07-10")
        self.assertEqual(bars[-1]["close"], 121_500)

    async def test_kospi_uses_ks_without_probing_kq(self):
        client = _Client(
            {
                ("005930.KS", "1d"): _chart(
                    _STAMPS, [277_500, 278_000, 287_250], ohlc=True, market_price=287_250
                )
            }
        )

        with patch.object(reports.httpx, "AsyncClient", lambda **_: client):
            bars = await reports._yahoo_prices("005930", "day")

        self.assertEqual(bars[-1]["close"], 287_250)
        self.assertEqual([symbol for symbol, _ in client.calls], ["005930.KS"])

    async def test_keeps_unverified_series_when_both_suffixes_disagree(self):
        """meta 가 비어 검증이 불가능해도 빈 차트로 떨어뜨리지 않는다(기존 동작 보존)."""
        client = _Client(
            {("005930.KS", "1d"): _chart(_STAMPS, [1, 2, 3], ohlc=True, market_price=None)}
        )

        with patch.object(reports.httpx, "AsyncClient", lambda **_: client):
            bars = await reports._yahoo_prices("005930", "day")

        self.assertEqual(len(bars), 3)
