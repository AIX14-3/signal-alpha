"""Tests for the DataLab keyword search-volume validation gate."""
from __future__ import annotations

import unittest

from app.clients.naver_datalab_client import NaverDataLabError, NaverDataLabRecord

import datalab_keyword_validator as gate


class FakeNaverClient:
    """Returns `active_days[keyword]` non-zero data points per keyword.

    A missing keyword -> raises (simulates an API failure). active_days==0 -> empty list
    (Naver omits zero-search days), which the gate must read as "no activity".
    """

    def __init__(self, active_days: dict[str, int], *, fail: set[str] | None = None):
        self._active_days = active_days
        self._fail = fail or set()
        self.calls: list[str] = []

    async def search(self, *, keyword_group, keywords, start_date, end_date, time_unit="date", **_):
        kw = keywords[0]
        self.calls.append(kw)
        if kw in self._fail:
            raise NaverDataLabError("simulated outage")
        n = self._active_days.get(kw, 0)
        return [
            NaverDataLabRecord(
                keyword_group=keyword_group, keyword=kw,
                period=f"2024-01-{d + 1:02d}", ratio=42.0, raw_data={},
            )
            for d in range(n)
        ]


def _draft(*keywords: str) -> dict:
    return {
        "ticker": "005930",
        "categories": [
            {
                "category_name": "SAMSUNG_DEMAND",
                "keyword_group": "SAMSUNG_DEMAND",
                "keywords": [{"text": k, "polarity": "demand"} for k in keywords],
            }
        ],
    }


class TierForActiveDaysTest(unittest.TestCase):
    def test_thresholds(self):
        # defaults: auto>=10, review>=3, else reject
        self.assertEqual(gate.tier_for_active_days(25), gate.TIER_AUTO)
        self.assertEqual(gate.tier_for_active_days(10), gate.TIER_AUTO)
        self.assertEqual(gate.tier_for_active_days(9), gate.TIER_REVIEW)
        self.assertEqual(gate.tier_for_active_days(3), gate.TIER_REVIEW)
        self.assertEqual(gate.tier_for_active_days(2), gate.TIER_REJECT)
        self.assertEqual(gate.tier_for_active_days(0), gate.TIER_REJECT)

    def test_custom_thresholds(self):
        self.assertEqual(gate.tier_for_active_days(5, auto_min=5, review_min=1), gate.TIER_AUTO)


class ValidateKeywordTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_days_counted_and_tiered(self):
        client = FakeNaverClient({"갤럭시 신제품": 20})
        verdict = await gate.validate_keyword(
            client, "갤럭시 신제품", start_date="2024-01-01", end_date="2024-01-30", window_days=30
        )
        self.assertEqual(verdict["active_days"], 20)
        self.assertEqual(verdict["coverage"], round(20 / 30, 3))
        self.assertEqual(verdict["tier"], gate.TIER_AUTO)
        self.assertFalse(verdict["error"])

    async def test_no_activity_is_reject(self):
        client = FakeNaverClient({"삼성전자 우주여행 리콜": 0})
        verdict = await gate.validate_keyword(
            client, "삼성전자 우주여행 리콜", start_date="2024-01-01", end_date="2024-01-30", window_days=30
        )
        self.assertEqual(verdict["active_days"], 0)
        self.assertEqual(verdict["tier"], gate.TIER_REJECT)

    async def test_api_error_routes_to_review(self):
        client = FakeNaverClient({}, fail={"불안정"})
        verdict = await gate.validate_keyword(
            client, "불안정", start_date="2024-01-01", end_date="2024-01-30", window_days=30
        )
        self.assertTrue(verdict["error"])
        self.assertEqual(verdict["tier"], gate.TIER_REVIEW)
        self.assertIsNone(verdict["active_days"])


class ValidateDraftVolumeTest(unittest.IsolatedAsyncioTestCase):
    async def test_annotates_drops_rejects_and_summarizes(self):
        client = FakeNaverClient({"강함": 20, "애매": 5, "죽음": 0})
        draft, summary = await gate.validate_draft_volume(_draft("강함", "애매", "죽음"), client, window_days=30)

        kept = {k["text"]: k for k in draft["categories"][0]["keywords"]}
        self.assertEqual(set(kept), {"강함", "애매"})  # "죽음" dropped
        self.assertEqual(kept["강함"]["validation"]["tier"], gate.TIER_AUTO)
        self.assertEqual(kept["애매"]["validation"]["tier"], gate.TIER_REVIEW)
        self.assertEqual(summary["auto"], 1)
        self.assertEqual(summary["review"], 1)
        self.assertEqual(summary["reject"], 1)
        self.assertEqual(summary["calls"], 3)

    async def test_empty_category_is_pruned(self):
        client = FakeNaverClient({"죽음1": 0, "죽음2": 1})
        draft, _ = await gate.validate_draft_volume(_draft("죽음1", "죽음2"), client, window_days=30)
        self.assertEqual(draft["categories"], [])

    async def test_max_calls_cap_routes_remaining_to_review(self):
        client = FakeNaverClient({"a": 20, "b": 20, "c": 20})
        draft, summary = await gate.validate_draft_volume(
            _draft("a", "b", "c"), client, window_days=30, max_calls=2
        )
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["capped"], 1)
        self.assertEqual(len(client.calls), 2)  # third keyword never hit the API
        capped = [k for k in draft["categories"][0]["keywords"] if k["validation"].get("capped")]
        self.assertEqual(len(capped), 1)
        self.assertEqual(capped[0]["validation"]["tier"], gate.TIER_REVIEW)


class RecordsClient:
    """Returns an explicit (period, ratio) series so recency/spike can be tested."""

    def __init__(self, series: list[tuple[str, float]]):
        self._series = series

    async def search(self, *, keyword_group, keywords, start_date, end_date, time_unit="date", **_):
        return [
            NaverDataLabRecord(
                keyword_group=keyword_group, keyword=keywords[0],
                period=p, ratio=r, raw_data={},
            )
            for p, r in self._series
        ]


class IsSpikeTest(unittest.TestCase):
    def test_requires_recency_and_concentration(self):
        self.assertTrue(gate.is_spike(3, 0.8))
        self.assertFalse(gate.is_spike(1, 0.9))   # too few recent active days
        self.assertFalse(gate.is_spike(3, 0.4))   # interest not concentrated recently


class SpikeFastTrackTest(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_spike_fasttracks_to_auto(self):
        # only 3 active days, all in the last week, all interest recent -> spike -> auto
        client = RecordsClient([("2024-01-28", 80.0), ("2024-01-29", 90.0), ("2024-01-30", 100.0)])
        v = await gate.validate_keyword(
            client, "신공장 화재", start_date="2024-01-01", end_date="2024-01-30", window_days=30
        )
        self.assertEqual(v["active_days"], 3)
        self.assertTrue(v["spike"])
        self.assertEqual(v["tier"], gate.TIER_AUTO)

    async def test_old_blip_is_not_spike(self):
        client = RecordsClient([("2024-01-02", 100.0), ("2024-01-03", 90.0)])
        v = await gate.validate_keyword(
            client, "옛날 블립", start_date="2024-01-01", end_date="2024-01-30", window_days=30
        )
        self.assertFalse(v["spike"])
        self.assertEqual(v["tier"], gate.TIER_REJECT)

    async def test_established_keyword_not_flagged_as_spike(self):
        series = [(f"2024-01-{d:02d}", 50.0) for d in range(1, 16)]  # 15 spread active days
        client = RecordsClient(series)
        v = await gate.validate_keyword(
            client, "삼성전자 주가", start_date="2024-01-01", end_date="2024-01-30", window_days=30
        )
        self.assertEqual(v["tier"], gate.TIER_AUTO)  # already auto by active-days
        self.assertFalse(v["spike"])                 # not a fast-track


if __name__ == "__main__":
    unittest.main()
