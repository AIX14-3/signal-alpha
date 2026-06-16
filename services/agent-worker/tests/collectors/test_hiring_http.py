"""hiring 공용 fetch 헬퍼(sites/http.py) retry/backoff 단위 테스트.

서드파티 mock 의존 없이 unittest.mock 으로 Session.get 과 time.sleep 을 패치한다.
지터(random.uniform)는 0으로 패치해 지수 백오프 정확값을 결정론적으로 단언한다.
"""

import unittest
from unittest import mock

import requests

from app.collectors.hiring.sites import http


class _FakeSettings:
    def __init__(self, *, retries=2, backoff=0.5, timeout=10.0):
        self.hiring_max_retries = retries
        self.hiring_retry_backoff_seconds = backoff
        self.hiring_timeout_seconds = timeout


def _http_error(status_code: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    exc = requests.HTTPError(response=resp)
    return exc


def _ok_response() -> mock.Mock:
    resp = mock.Mock(spec=requests.Response)
    resp.raise_for_status.return_value = None
    return resp


class HiringHttpRetryTest(unittest.TestCase):
    def setUp(self):
        # 실제 대기/지터 제거 → 빠르고 결정론적.
        self._sleep = mock.patch.object(http.time, "sleep").start()
        mock.patch.object(http.random, "uniform", return_value=0.0).start()
        self.addCleanup(mock.patch.stopall)

    def _patch_session(self, side_effect):
        session = mock.Mock()
        session.get.side_effect = side_effect
        return mock.patch.object(http, "_get_session", return_value=session), session

    def test_retries_then_succeeds_after_transient_timeouts(self):
        ok = _ok_response()
        patcher, session = self._patch_session([requests.Timeout(), requests.Timeout(), ok])
        with patcher:
            result = http.get("https://x.test", settings=_FakeSettings(retries=2, backoff=0.5))

        self.assertIs(result, ok)
        self.assertEqual(session.get.call_count, 3)  # 2 실패 + 1 성공
        # 지수 백오프: attempt0 → 0.5*1, attempt1 → 0.5*2 (지터 0).
        self.assertEqual(
            [c.args[0] for c in self._sleep.call_args_list], [0.5, 1.0]
        )

    def test_raises_after_exhausting_retries(self):
        patcher, session = self._patch_session(requests.Timeout())
        with patcher, self.assertRaises(requests.Timeout):
            http.get("https://x.test", settings=_FakeSettings(retries=2))
        self.assertEqual(session.get.call_count, 3)  # retries+1

    def test_4xx_is_not_retried(self):
        resp = _ok_response()
        resp.raise_for_status.side_effect = _http_error(404)
        patcher, session = self._patch_session([resp])
        with patcher, self.assertRaises(requests.HTTPError):
            http.get("https://x.test", settings=_FakeSettings(retries=2))
        self.assertEqual(session.get.call_count, 1)  # 즉시 raise
        self._sleep.assert_not_called()

    def test_5xx_is_retried(self):
        bad = _ok_response()
        bad.raise_for_status.side_effect = _http_error(503)
        good = _ok_response()
        patcher, session = self._patch_session([bad, good])
        with patcher:
            result = http.get("https://x.test", settings=_FakeSettings(retries=2))
        self.assertIs(result, good)
        self.assertEqual(session.get.call_count, 2)

    def test_zero_retries_calls_once(self):
        patcher, session = self._patch_session(requests.ConnectionError())
        with patcher, self.assertRaises(requests.ConnectionError):
            http.get("https://x.test", settings=_FakeSettings(retries=0))
        self.assertEqual(session.get.call_count, 1)
        self._sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
