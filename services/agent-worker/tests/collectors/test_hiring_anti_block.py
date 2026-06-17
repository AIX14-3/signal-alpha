"""hiring anti-block(#146) 단위 테스트 — UA 로테이션 + 429/403 적응형 백오프.

서드파티 mock 의존 없이 unittest.mock으로 Session.get·time.sleep·pick_ua/random을
패치한다. 지수 백오프 정확값 단언을 위해 지터(random.uniform)는 0으로 패치한다.
"""

import unittest
from unittest import mock

import requests

from app.collectors.hiring import user_agents
from app.collectors.hiring.sites import http


class _FakeSettings:
    def __init__(self, *, retries=2, backoff=0.5, timeout=10.0, cap=30.0, ua_pool=None):
        self.hiring_max_retries = retries
        self.hiring_retry_backoff_seconds = backoff
        self.hiring_timeout_seconds = timeout
        self.hiring_rate_limit_max_backoff_seconds = cap
        self.hiring_ua_pool = ua_pool or ["UA-a", "UA-b", "UA-c"]


def _resp(status_code: int, *, retry_after: str | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    if retry_after is not None:
        resp.headers["Retry-After"] = retry_after
    return resp


def _http_error(status_code: int, *, retry_after: str | None = None) -> requests.HTTPError:
    return requests.HTTPError(response=_resp(status_code, retry_after=retry_after))


class PickUaTest(unittest.TestCase):
    def test_pick_ua_from_pool(self):
        cfg = _FakeSettings(ua_pool=["only-ua"])
        self.assertEqual(user_agents.pick_ua(cfg), "only-ua")

    def test_pick_ua_fallback_when_pool_empty(self):
        cfg = _FakeSettings(ua_pool=[])
        cfg.hiring_ua_pool = []
        self.assertEqual(user_agents.pick_ua(cfg), user_agents._FALLBACK_UA)


class UaRotationTest(unittest.TestCase):
    def setUp(self):
        mock.patch.object(http.time, "sleep").start()
        mock.patch.object(http.random, "uniform", return_value=0.0).start()
        self.addCleanup(mock.patch.stopall)

    def test_ua_injected_and_rotated_across_retries(self):
        # pick_ua가 시도마다 다른 값을 내도록 패치 → 헤더 로테이션 검증.
        uas = iter(["UA-1", "UA-2", "UA-3"])
        mock.patch.object(http, "pick_ua", side_effect=lambda cfg: next(uas)).start()

        session = mock.Mock()
        session.get.side_effect = [requests.Timeout(), requests.Timeout(), _ok()]
        with mock.patch.object(http, "_get_session", return_value=session):
            http.get("https://x.test", headers={"Referer": "r"}, settings=_FakeSettings(retries=2))

        sent_uas = [c.kwargs["headers"]["User-Agent"] for c in session.get.call_args_list]
        self.assertEqual(sent_uas, ["UA-1", "UA-2", "UA-3"])  # 매 시도 로테이션
        # 호출부 헤더(Referer)는 보존.
        self.assertEqual(session.get.call_args_list[0].kwargs["headers"]["Referer"], "r")


class AdaptiveBackoffTest(unittest.TestCase):
    def setUp(self):
        self._sleep = mock.patch.object(http.time, "sleep").start()
        mock.patch.object(http.random, "uniform", return_value=0.0).start()
        mock.patch.object(http, "pick_ua", return_value="UA-x").start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, side_effect, settings):
        session = mock.Mock()
        session.get.side_effect = side_effect
        with mock.patch.object(http, "_get_session", return_value=session):
            return http.get("https://x.test", settings=settings), session

    def _bad(self, status_code, *, retry_after=None):
        resp = _ok()
        resp.raise_for_status.side_effect = _http_error(status_code, retry_after=retry_after)
        return resp

    def test_429_honors_retry_after(self):
        _, session = self._run([self._bad(429, retry_after="2"), _ok()], _FakeSettings(retries=2, cap=30))
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(self._sleep.call_args_list[0].args[0], 2.0)  # Retry-After 존중

    def test_429_retry_after_capped(self):
        self._run([self._bad(429, retry_after="999"), _ok()], _FakeSettings(retries=2, cap=30))
        self.assertEqual(self._sleep.call_args_list[0].args[0], 30.0)  # cap 적용

    def test_429_without_retry_after_uses_exponential(self):
        self._run([self._bad(429), _ok()], _FakeSettings(retries=2, backoff=0.5, cap=30))
        self.assertEqual(self._sleep.call_args_list[0].args[0], 0.5)  # backoff*2^0

    def test_403_is_retried(self):
        _, session = self._run([self._bad(403), _ok()], _FakeSettings(retries=2))
        self.assertEqual(session.get.call_count, 2)

    def test_404_still_raises_immediately(self):
        with self.assertRaises(requests.HTTPError):
            self._run([self._bad(404)], _FakeSettings(retries=2))
        self._sleep.assert_not_called()


def _ok() -> mock.Mock:
    resp = mock.Mock(spec=requests.Response)
    resp.raise_for_status.return_value = None
    return resp


if __name__ == "__main__":
    unittest.main()
