import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.guard.daemon import GuardRuntimeStatus, _news_timespan, run_guard_cycle
from app.guard.gdelt import GuardArticle, GuardCollectError


def _settings(**overrides):
    values = {
        "guard_severity_threshold": 70,
        "guard_auto_max_scope": "report_generation",
        "guard_auto_cooldown_sec": 3600.0,
        "guard_keywords": ["war"],
        "guard_news_max_articles": 25,
        "guard_llm_timeout_seconds": 20.0,
        "guard_poll_interval_sec": 900.0,
        "gemini_api_key": "",
        "guard_llm_model": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _article(seq):
    return GuardArticle(
        source="gdelt",
        article_hash=f"{seq:064x}",
        title=f"기사 {seq}",
        url=f"https://news.example/{seq}",
        published_at=datetime.now(UTC),
    )


def _valid_payload(severity=82):
    return {
        "severity": severity,
        "is_geopolitical_risk": True,
        "direction": "escalation",
        "summary": "이란-미국 분쟁 확전 속보.",
        "regions": ["Iran"],
        "affected_themes": ["oil"],
        "confidence": 75,
        "evidence": [],
    }


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    def __init__(self, known_hashes=()):
        self.known_hashes = set(known_hashes)
        self.news_rows = []
        self.executed = []
        self.status_row = {
            "status": "ok",
            "scope": "report_generation",
            "mode": "advisory",
            "triggered_by": None,
            "updated_at": datetime.now(UTC) - timedelta(hours=2),
        }
        self.next_event_id = 100

    def transaction(self):
        return _FakeTransaction()

    async def fetch(self, sql, *args):
        if "FROM guard_news_events" in sql:
            return [{"article_hash": h} for h in args[0] if h in self.known_hashes]
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def fetchrow(self, sql, *args):
        if "FROM guard_site_status" in sql:
            return self.status_row
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetchval(self, sql, *args):
        if "INSERT INTO guard_news_events" in sql:
            self.news_rows.append(args)
            self.next_event_id += 1
            return self.next_event_id
        if "FROM guard_recommendations" in sql:
            return None
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"


class _FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _FakeAcquire(self.connection)


class _FakeLlm:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def generate_json(self, prompt):
        self.calls += 1
        return self.payload


class RunGuardCycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_articles_skips_llm_and_db(self):
        conn = FakeConnection()
        llm = _FakeLlm(_valid_payload())

        async def fetch_none(settings):
            return []

        summary = await run_guard_cycle(
            FakePool(conn), _settings(), fetch_articles=fetch_none, llm_factory=lambda s: llm
        )
        self.assertEqual(summary, {"collected": 0, "new": 0, "action": "none"})
        self.assertEqual(llm.calls, 0)
        self.assertEqual(conn.news_rows, [])

    async def test_all_known_articles_skip_llm(self):
        articles = [_article(1), _article(2)]
        conn = FakeConnection(known_hashes={a.article_hash for a in articles})
        llm = _FakeLlm(_valid_payload())

        async def fetch(settings):
            return articles

        summary = await run_guard_cycle(
            FakePool(conn), _settings(), fetch_articles=fetch, llm_factory=lambda s: llm
        )
        self.assertEqual(summary["new"], 0)
        self.assertEqual(llm.calls, 0)

    async def test_fresh_articles_are_judged_stored_and_gated(self):
        articles = [_article(1), _article(2), _article(3)]
        conn = FakeConnection(known_hashes={articles[0].article_hash})
        llm = _FakeLlm(_valid_payload(severity=82))

        async def fetch(settings):
            return articles

        summary = await run_guard_cycle(
            FakePool(conn), _settings(), fetch_articles=fetch, llm_factory=lambda s: llm
        )
        self.assertEqual(summary["collected"], 3)
        self.assertEqual(summary["new"], 2)
        self.assertEqual(summary["action"], "recommended")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(len(conn.news_rows), 2)
        # advisory 기본 모드 — 상태 UPDATE 없이 제안만 적재된다.
        inserts = [sql for sql, _ in conn.executed if "guard_recommendations" in sql]
        updates = [sql for sql, _ in conn.executed if "UPDATE guard_site_status" in sql]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(updates, [])

    async def test_collect_failure_leaves_state_unchanged(self):
        conn = FakeConnection()

        async def fetch_boom(settings):
            raise GuardCollectError("gdelt down")

        with self.assertRaises(GuardCollectError):
            await run_guard_cycle(FakePool(conn), _settings(), fetch_articles=fetch_boom)
        self.assertEqual(conn.executed, [])
        self.assertEqual(conn.news_rows, [])

    async def test_llm_failure_leaves_state_unchanged(self):
        articles = [_article(1)]
        conn = FakeConnection()

        async def fetch(settings):
            return articles

        class _BoomLlm:
            async def generate_json(self, prompt):
                raise RuntimeError("llm down")

        with self.assertRaises(RuntimeError):
            await run_guard_cycle(
                FakePool(conn), _settings(), fetch_articles=fetch, llm_factory=lambda s: _BoomLlm()
            )
        self.assertEqual(conn.executed, [])
        self.assertEqual(conn.news_rows, [])


class GuardRuntimeStatusTest(unittest.TestCase):
    def test_snapshot_tracks_cycles_and_errors(self):
        status = GuardRuntimeStatus()
        status.mark_started()
        status.mark_cycle({"new": 1})
        self.assertEqual(status.snapshot()["cycles_completed"], 1)
        status.mark_error(RuntimeError("boom"))
        self.assertEqual(status.snapshot()["last_error"], "boom")


class NewsTimespanTest(unittest.TestCase):
    def test_floor_is_60_minutes(self):
        # 15분 주기여도 GDELT 갱신 지연을 고려해 최소 60분은 되돌아본다.
        self.assertEqual(_news_timespan(SimpleNamespace(guard_poll_interval_sec=900)), "60min")

    def test_scales_to_twice_the_interval(self):
        # 2시간 주기면 4시간(240분) lookback 으로 공백을 덮는다.
        self.assertEqual(_news_timespan(SimpleNamespace(guard_poll_interval_sec=7200)), "240min")


if __name__ == "__main__":
    unittest.main()
