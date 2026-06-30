"""NaverDataLabClient._post_json 재시도 정책 — 4xx fail-fast, 5xx 백오프 재시도."""
from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabError


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://openapi.naver.com", code, "err", {}, None)  # type: ignore[arg-type]


class TestNaverRetryPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.client = NaverDataLabClient(client_id="id", client_secret="sec")

    def test_429_fails_fast_without_retry(self):
        # 429(쿼터/레이트 소진)는 1~2초 백오프로 회복 안 되므로 즉시 실패해야 한다.
        with patch("app.clients.naver_datalab_client.urlopen", side_effect=_http_error(429)) as m, \
                patch("app.clients.naver_datalab_client.time.sleep") as sleep:
            with self.assertRaises(NaverDataLabError):
                self.client._post_json({"body": 1})
            self.assertEqual(m.call_count, 1, "429 는 재시도하지 말아야 한다")
            sleep.assert_not_called()

    def test_other_4xx_fails_fast(self):
        with patch("app.clients.naver_datalab_client.urlopen", side_effect=_http_error(400)) as m, \
                patch("app.clients.naver_datalab_client.time.sleep"):
            with self.assertRaises(NaverDataLabError):
                self.client._post_json({"body": 1})
            self.assertEqual(m.call_count, 1)

    def test_5xx_retries_then_fails(self):
        # 서버 일시 오류(5xx)는 백오프 재시도 후 실패.
        with patch("app.clients.naver_datalab_client.urlopen", side_effect=_http_error(503)) as m, \
                patch("app.clients.naver_datalab_client.time.sleep") as sleep:
            with self.assertRaises(NaverDataLabError):
                self.client._post_json({"body": 1})
            self.assertEqual(m.call_count, NaverDataLabClient.MAX_RETRIES)
            self.assertEqual(sleep.call_count, NaverDataLabClient.MAX_RETRIES - 1)


if __name__ == "__main__":
    unittest.main()
