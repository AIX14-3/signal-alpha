"""Tests for the Naver-news event source + grounding enrichment."""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import keyword_gen_common
import news_event_source as nes

STOCK = {"id": 1, "ticker": "005930", "name": "삼성전자", "sector": "전자"}


def _recent_pubdate(days_ago: int) -> str:
    return format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago))


def _item(title: str, desc: str, days_ago: int) -> dict:
    return {"title": title, "description": desc, "pubDate": _recent_pubdate(days_ago)}


class CleanTest(unittest.TestCase):
    def test_strips_tags_and_entities(self):
        self.assertEqual(nes._clean("<b>갤럭시</b> S25 &quot;출시&quot;"), '갤럭시 S25 "출시"')
        self.assertEqual(nes._clean(None), "")


class FetchRecentNewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_maps_to_event_contract(self):
        items = [_item("<b>삼성전자</b> 갤럭시 S25 출시", "신제품 <b>공개</b>", 1)]
        nes._request_news = lambda q, *, display: items  # type: ignore[assignment]
        events = await nes.fetch_recent_news(STOCK, days=14, max_items=20)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["report_name"], "삼성전자 갤럭시 S25 출시")
        self.assertEqual(ev["disclosure_type"], "NEWS")
        self.assertEqual(ev["priority_reason"], "신제품 공개")
        self.assertIsNotNone(ev["created_at"])

    async def test_filters_out_of_window_and_dedupes(self):
        items = [
            _item("같은 제목", "본문 A", 1),
            _item("같은 제목", "본문 B", 2),     # duplicate title -> dropped
            _item("오래된 뉴스", "본문 C", 400),  # out of 14d window -> dropped
        ]
        nes._request_news = lambda q, *, display: items  # type: ignore[assignment]
        events = await nes.fetch_recent_news(STOCK, days=14, max_items=20)
        titles = [e["report_name"] for e in events]
        self.assertEqual(titles, ["같은 제목"])

    async def test_max_items_caps(self):
        items = [_item(f"뉴스{i}", "본문", 1) for i in range(5)]
        nes._request_news = lambda q, *, display: items  # type: ignore[assignment]
        events = await nes.fetch_recent_news(STOCK, days=14, max_items=2)
        self.assertEqual(len(events), 2)


class EnrichEventsTest(unittest.TestCase):
    def test_appends_brief_event(self):
        nes.call_gemini_grounded = lambda prompt: "- 갤럭시 S25: 2026-01 출시, 엑시노스 2500 탑재"  # type: ignore[assignment]
        events = [{"report_name": "갤럭시 S25 출시", "disclosure_type": "NEWS"}]
        out = nes.enrich_events(STOCK, events)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1]["disclosure_type"], "BRIEF")
        self.assertIn("엑시노스", out[-1]["priority_reason"])

    def test_falls_back_on_grounding_error(self):
        def boom(prompt):
            raise RuntimeError("grounding down")

        nes.call_gemini_grounded = boom  # type: ignore[assignment]
        events = [{"report_name": "갤럭시 S25 출시", "disclosure_type": "NEWS"}]
        self.assertEqual(nes.enrich_events(STOCK, events), events)

    def test_empty_brief_is_ignored(self):
        nes.call_gemini_grounded = lambda prompt: "   "  # type: ignore[assignment]
        events = [{"report_name": "x", "disclosure_type": "NEWS"}]
        self.assertEqual(nes.enrich_events(STOCK, events), events)


class GatherEventsTest(unittest.IsolatedAsyncioTestCase):
    async def test_news_only_no_grounding(self):
        nes._request_news = lambda q, *, display: [_item("뉴스1", "본문", 1)]  # type: ignore[assignment]
        events = await nes.gather_events(STOCK, days=365, source="news", ground=False)
        self.assertEqual([e["report_name"] for e in events], ["뉴스1"])

    async def test_news_with_grounding_appends_brief(self):
        nes._request_news = lambda q, *, display: [_item("뉴스1", "본문", 1)]  # type: ignore[assignment]
        nes.call_gemini_grounded = lambda prompt: "팩트 브리프"  # type: ignore[assignment]
        events = await nes.gather_events(STOCK, days=365, source="news", ground=True)
        self.assertEqual(events[-1]["disclosure_type"], "BRIEF")


class CallGeminiGroundedTest(unittest.TestCase):
    def test_sends_search_tool_without_json_mime(self):
        captured = {}

        class FakeResp:
            def __init__(self, payload):
                self._b = json.dumps(payload).encode("utf-8")

            def read(self):
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp(
                {"candidates": [{"content": {"parts": [{"text": "브리프 본문"}]}}]}
            )

        os.environ["GEMINI_API_KEY"] = "test-key"
        keyword_gen_common.urlopen = fake_urlopen  # type: ignore[assignment]
        text = keyword_gen_common.call_gemini_grounded("hello")
        self.assertEqual(text, "브리프 본문")
        self.assertEqual(captured["body"]["tools"], [{"google_search": {}}])
        self.assertNotIn("responseMimeType", captured["body"]["generationConfig"])


if __name__ == "__main__":
    unittest.main()
