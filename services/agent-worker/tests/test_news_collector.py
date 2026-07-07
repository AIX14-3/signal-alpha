import unittest
from datetime import UTC, datetime, timedelta

from app.clients.naver_news_client import NaverNewsItem
from app.news.collector import clean_text, collect_stock_news, compute_article_hash

_NOW = datetime(2026, 7, 7, 0, 0, tzinfo=UTC)


def _rfc2822(dt: datetime) -> str:
    # 예: "Mon, 06 Jul 2026 09:00:00 +0000"
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


class FakeNewsClient:
    def __init__(self, items):
        self._items = items
        self.calls = []

    async def search(self, query, *, display=20, sort="date"):
        self.calls.append({"query": query, "display": display, "sort": sort})
        return self._items


class CleanTextTest(unittest.TestCase):
    def test_strips_tags_and_unescapes(self):
        self.assertEqual(clean_text("<b>SK</b>하이닉스 &quot;급등&quot;"), 'SK하이닉스 "급등"')

    def test_none_is_empty(self):
        self.assertEqual(clean_text(None), "")


class HashTest(unittest.TestCase):
    def test_url_preferred_over_title(self):
        self.assertEqual(
            compute_article_hash("https://n.example/1", "제목"),
            compute_article_hash("https://n.example/1", "다른제목"),
        )

    def test_falls_back_to_title(self):
        self.assertEqual(compute_article_hash(None, "제목"), compute_article_hash("", "제목"))


class CollectStockNewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_maps_to_news_item_contract(self):
        client = FakeNewsClient(
            [
                NaverNewsItem(
                    title="<b>SK하이닉스</b> 신고가",
                    description="반도체 <b>업황</b> 개선 기대",
                    url="https://news.example/1",
                    pub_date=_rfc2822(_NOW - timedelta(hours=2)),
                )
            ]
        )
        items = await collect_stock_news(client, "SK하이닉스", lookback_days=14, max_items=20, now=_NOW)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "SK하이닉스 신고가")
        self.assertEqual(item.summary, "반도체 업황 개선 기대")
        self.assertEqual(item.url, "https://news.example/1")
        self.assertIsNotNone(item.published_at)
        self.assertEqual(item.article_hash, compute_article_hash("https://news.example/1", "SK하이닉스 신고가"))

    async def test_filters_out_of_window_but_keeps_undated(self):
        client = FakeNewsClient(
            [
                NaverNewsItem("최근", "d", "https://news.example/recent", _rfc2822(_NOW - timedelta(days=1))),
                NaverNewsItem("오래됨", "d", "https://news.example/old", _rfc2822(_NOW - timedelta(days=40))),
                NaverNewsItem("날짜없음", "d", "https://news.example/none", None),
            ]
        )
        items = await collect_stock_news(client, "쿼리", lookback_days=14, max_items=20, now=_NOW)
        titles = {i.title for i in items}
        self.assertIn("최근", titles)
        self.assertIn("날짜없음", titles)
        self.assertNotIn("오래됨", titles)

    async def test_dedupes_by_title(self):
        client = FakeNewsClient(
            [
                NaverNewsItem("같은제목", "a", "https://news.example/1", _rfc2822(_NOW)),
                NaverNewsItem("같은제목", "b", "https://news.example/2", _rfc2822(_NOW)),
            ]
        )
        items = await collect_stock_news(client, "쿼리", lookback_days=14, max_items=20, now=_NOW)
        self.assertEqual(len(items), 1)

    async def test_caps_at_max_items(self):
        client = FakeNewsClient(
            [
                NaverNewsItem(f"제목{n}", "d", f"https://news.example/{n}", _rfc2822(_NOW))
                for n in range(10)
            ]
        )
        items = await collect_stock_news(client, "쿼리", lookback_days=14, max_items=3, now=_NOW)
        self.assertEqual(len(items), 3)

    async def test_empty_query_returns_empty(self):
        client = FakeNewsClient([NaverNewsItem("t", "d", "u", _rfc2822(_NOW))])
        items = await collect_stock_news(client, "   ", lookback_days=14, max_items=20, now=_NOW)
        self.assertEqual(items, [])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
