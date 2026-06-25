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
        # anti-block(#146): UA 주입/429-403 적응형 백오프가 참조하는 속성.
        self.hiring_ua_pool = ["UA-test/1.0"]
        self.hiring_rate_limit_max_backoff_seconds = 30.0


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


class HiringHttpBlockSensorTest(unittest.TestCase):
    """차단 신호 센서(#162 트리거 계측): requests 403/429만 시도 단위로 집계."""

    def setUp(self):
        mock.patch.object(http.time, "sleep").start()
        mock.patch.object(http.random, "uniform", return_value=0.0).start()
        self.addCleanup(mock.patch.stopall)
        http.reset_block_signals()
        self.addCleanup(http.reset_block_signals)

    def _patch_session(self, side_effect):
        session = mock.Mock()
        session.get.side_effect = side_effect
        return mock.patch.object(http, "_get_session", return_value=session), session

    def test_403_recorded_per_attempt(self):
        resp = _ok_response()
        resp.raise_for_status.side_effect = _http_error(403)
        patcher, session = self._patch_session([resp, resp, resp])  # 매 시도 403
        with patcher, self.assertRaises(requests.HTTPError):
            http.get("https://x.test", settings=_FakeSettings(retries=2))
        self.assertEqual(session.get.call_count, 3)         # retries+1
        self.assertEqual(http.block_signal_snapshot(), {"403": 3, "429": 0})

    def test_429_recorded(self):
        resp = _ok_response()
        resp.raise_for_status.side_effect = _http_error(429)
        patcher, _ = self._patch_session([resp])
        with patcher, self.assertRaises(requests.HTTPError):
            http.get("https://x.test", settings=_FakeSettings(retries=0))
        self.assertEqual(http.block_signal_snapshot(), {"403": 0, "429": 1})

    def test_non_block_4xx_not_recorded(self):
        resp = _ok_response()
        resp.raise_for_status.side_effect = _http_error(404)
        patcher, _ = self._patch_session([resp])
        with patcher, self.assertRaises(requests.HTTPError):
            http.get("https://x.test", settings=_FakeSettings(retries=2))
        self.assertEqual(http.block_signal_snapshot(), {"403": 0, "429": 0})

    def test_timeout_not_recorded(self):
        patcher, _ = self._patch_session(requests.Timeout())
        with patcher, self.assertRaises(requests.Timeout):
            http.get("https://x.test", settings=_FakeSettings(retries=1))
        self.assertEqual(http.block_signal_snapshot(), {"403": 0, "429": 0})

    def test_reset_clears_counts(self):
        http.record_block_signal(403)
        http.record_block_signal(429)
        http.reset_block_signals()
        self.assertEqual(http.block_signal_snapshot(), {"403": 0, "429": 0})

    def test_retry_delay_has_no_counting_side_effect(self):
        """_retry_delay 는 순수 판정 함수 — 카운팅 부작용이 없어야 한다."""
        http._retry_delay(_http_error(403), 0, _FakeSettings())
        http._retry_delay(_http_error(429), 0, _FakeSettings())
        self.assertEqual(http.block_signal_snapshot(), {"403": 0, "429": 0})

    def test_snapshot_is_a_copy(self):
        """snapshot 변형이 내부 상태를 오염시키지 않아야 한다."""
        snap = http.block_signal_snapshot()
        snap["403"] = 999
        self.assertEqual(http.block_signal_snapshot(), {"403": 0, "429": 0})


class HiringHttpPostTest(unittest.TestCase):
    """post() 헬퍼: session.post 위임 + json 본문 전달 + retry 머신 공유."""

    def setUp(self):
        self._sleep = mock.patch.object(http.time, "sleep").start()
        mock.patch.object(http.random, "uniform", return_value=0.0).start()
        self.addCleanup(mock.patch.stopall)

    def test_post_passes_json_body(self):
        ok = _ok_response()
        session = mock.Mock()
        session.post.side_effect = [ok]
        with mock.patch.object(http, "_get_session", return_value=session):
            result = http.post(
                "https://x.test", json={"a": 1}, settings=_FakeSettings(retries=0)
            )
        self.assertIs(result, ok)
        session.post.assert_called_once()
        self.assertEqual(session.post.call_args.kwargs["json"], {"a": 1})
        session.get.assert_not_called()

    def test_post_retries_on_5xx(self):
        bad = _ok_response()
        bad.raise_for_status.side_effect = _http_error(503)
        good = _ok_response()
        session = mock.Mock()
        session.post.side_effect = [bad, good]
        with mock.patch.object(http, "_get_session", return_value=session):
            result = http.post("https://x.test", json={}, settings=_FakeSettings(retries=2))
        self.assertIs(result, good)
        self.assertEqual(session.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
