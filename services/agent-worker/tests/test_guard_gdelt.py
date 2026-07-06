import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.guard.gdelt import compute_article_hash, parse_gdelt_articles


class ParseGdeltArticlesTest(unittest.TestCase):
    def test_parses_articles_with_seendate(self):
        payload = {
            "articles": [
                {
                    "url": "https://news.example/a",
                    "title": "Ceasefire talks collapse",
                    "seendate": "20260703T114500Z",
                },
                {"url": "https://news.example/b", "title": "Oil jumps 9%"},
            ]
        }
        articles = parse_gdelt_articles(payload)
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].source, "gdelt")
        self.assertEqual(
            articles[0].published_at, datetime(2026, 7, 3, 11, 45, tzinfo=UTC)
        )
        self.assertIsNone(articles[1].published_at)

    def test_dedupes_same_url_within_batch(self):
        payload = {
            "articles": [
                {"url": "https://news.example/a", "title": "first"},
                {"url": "https://news.example/a", "title": "duplicate"},
            ]
        }
        self.assertEqual(len(parse_gdelt_articles(payload)), 1)

    def test_skips_items_without_url_and_title(self):
        payload = {"articles": [{"seendate": "20260703T114500Z"}, "garbage", None]}
        self.assertEqual(parse_gdelt_articles(payload), [])

    def test_empty_or_missing_payload(self):
        self.assertEqual(parse_gdelt_articles(None), [])
        self.assertEqual(parse_gdelt_articles({}), [])

    def test_hash_prefers_url_over_title(self):
        with_url = compute_article_hash("https://news.example/a", "title-1")
        same_url_other_title = compute_article_hash("https://news.example/a", "title-2")
        self.assertEqual(with_url, same_url_other_title)
        self.assertNotEqual(with_url, compute_article_hash(None, "title-1"))


if __name__ == "__main__":
    unittest.main()
