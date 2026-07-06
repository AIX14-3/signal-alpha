import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.guard.gate import AGENT_ACTOR, apply_judgment, severity_to_scope
from app.guard.judge import GeoRiskJudgment


def _judgment(**overrides):
    values = {
        "severity": 82,
        "is_geopolitical_risk": True,
        "direction": "escalation",
        "summary": "이란-미국 분쟁 확전 속보.",
        "regions": ["Iran", "US"],
        "affected_themes": ["oil"],
        "confidence": 75,
        "evidence": ["추가 공습 발표"],
    }
    values.update(overrides)
    return GeoRiskJudgment(**values)


def _settings(**overrides):
    values = {
        "guard_severity_threshold": 70,
        "guard_auto_max_scope": "report_generation",
        "guard_auto_cooldown_sec": 3600.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeConnection:
    def __init__(self, status_row, *, pending_scopes=()):
        self.status_row = status_row
        self.pending_scopes = set(pending_scopes)
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        if "FROM guard_site_status" in sql:
            return self.status_row
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetchval(self, sql, *args):
        if "FROM guard_recommendations" in sql:
            return 1 if args[0] in self.pending_scopes else None
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"

    def executed_matching(self, fragment):
        return [(sql, args) for sql, args in self.executed if fragment in sql]


def _status_row(**overrides):
    row = {
        "status": "ok",
        "scope": "report_generation",
        "mode": "advisory",
        "triggered_by": None,
        "updated_at": datetime.now(UTC) - timedelta(hours=2),
    }
    row.update(overrides)
    return row


class SeverityToScopeTest(unittest.TestCase):
    def test_mapping_bands(self):
        self.assertIsNone(severity_to_scope(49))
        self.assertEqual(severity_to_scope(50), "report_generation")
        self.assertEqual(severity_to_scope(70), "report_view")
        self.assertEqual(severity_to_scope(90), "whole_site")


class ApplyJudgmentTest(unittest.IsolatedAsyncioTestCase):
    async def test_below_threshold_is_noop(self):
        conn = FakeConnection(_status_row())
        result = await apply_judgment(conn, _settings(), _judgment(severity=40), news_event_id=1)
        self.assertEqual(result["action"], "none")
        self.assertEqual(conn.executed, [])

    async def test_manual_mode_ignores_agent(self):
        conn = FakeConnection(_status_row(mode="manual"))
        result = await apply_judgment(conn, _settings(), _judgment(), news_event_id=1)
        self.assertEqual(result["action"], "ignored_manual")
        self.assertEqual(conn.executed, [])

    async def test_advisory_creates_pending_recommendation_only(self):
        conn = FakeConnection(_status_row(mode="advisory"))
        result = await apply_judgment(conn, _settings(), _judgment(severity=82), news_event_id=7)
        self.assertEqual(result, {"action": "recommended", "scope": "report_view"})
        self.assertEqual(len(conn.executed_matching("INSERT INTO guard_recommendations")), 1)
        self.assertEqual(conn.executed_matching("UPDATE guard_site_status"), [])

    async def test_advisory_refreshes_pending_recommendation(self):
        # 같은 scope 의 pending 제안이 있으면 새 카드를 쌓지 않고 최신 판정으로 갱신한다
        # (박제된 옛 사건 대신 지금 사건이 카드에 반영되도록).
        conn = FakeConnection(_status_row(mode="advisory"), pending_scopes={"report_view"})
        result = await apply_judgment(conn, _settings(), _judgment(severity=82), news_event_id=7)
        self.assertEqual(result["action"], "recommendation_refreshed")
        # 갱신은 UPDATE ... RETURNING(fetchval) 으로 처리 — 새 INSERT 는 없다.
        self.assertEqual(conn.executed_matching("INSERT INTO guard_recommendations"), [])

    async def test_auto_blocks_within_scope_ceiling(self):
        conn = FakeConnection(_status_row(mode="auto"))
        result = await apply_judgment(conn, _settings(), _judgment(severity=82), news_event_id=7)
        # severity 82 → report_view 지만 auto 상한(report_generation)으로 캡.
        self.assertEqual(result, {"action": "auto_blocked", "scope": "report_generation"})
        update_sql, update_args = conn.executed_matching("UPDATE guard_site_status")[0]
        self.assertEqual(update_args[0], "report_generation")
        self.assertEqual(update_args[2], AGENT_ACTOR)
        self.assertEqual(len(conn.executed_matching("INSERT INTO guard_status_audit")), 1)

    async def test_auto_whole_site_requires_human_approval(self):
        conn = FakeConnection(_status_row(mode="auto"))
        result = await apply_judgment(conn, _settings(), _judgment(severity=95), news_event_id=7)
        self.assertEqual(result["action"], "recommended")
        self.assertEqual(result["scope"], "whole_site")
        self.assertEqual(conn.executed_matching("UPDATE guard_site_status"), [])

    async def test_auto_cooldown_defers_to_recommendation(self):
        # 쿨다운 중엔 자동 차단을 못 걸어도 신호를 삼키지 않고 제안으로 남긴다
        # (급격한 확전이 최대 쿨다운 동안 유실되지 않도록).
        conn = FakeConnection(
            _status_row(
                mode="auto",
                status="ok",
                triggered_by=AGENT_ACTOR,
                updated_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        result = await apply_judgment(conn, _settings(), _judgment(severity=82), news_event_id=7)
        self.assertEqual(result["auto_deferred"], "cooldown")
        self.assertEqual(result["action"], "recommended")
        # 상태(guard_site_status)는 건드리지 않고 제안만 적재.
        self.assertEqual(conn.executed_matching("UPDATE guard_site_status"), [])
        self.assertEqual(len(conn.executed_matching("INSERT INTO guard_recommendations")), 1)

    async def test_auto_skips_when_already_blocked_at_or_above(self):
        conn = FakeConnection(
            _status_row(mode="auto", status="blocked", scope="report_view", triggered_by=AGENT_ACTOR)
        )
        result = await apply_judgment(conn, _settings(), _judgment(severity=82), news_event_id=7)
        self.assertEqual(result["action"], "already_blocked")
        self.assertEqual(conn.executed, [])

    async def test_auto_releases_on_deescalation_after_cooldown(self):
        conn = FakeConnection(
            _status_row(
                mode="auto",
                status="blocked",
                scope="report_generation",
                triggered_by=AGENT_ACTOR,
                updated_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        result = await apply_judgment(
            conn, _settings(), _judgment(severity=20, direction="deescalation"), news_event_id=7
        )
        self.assertEqual(result["action"], "auto_released")
        update_sql, update_args = conn.executed_matching("UPDATE guard_site_status")[0]
        self.assertIn("status = 'ok'", update_sql)

    async def test_auto_never_releases_manual_admin_block(self):
        conn = FakeConnection(
            _status_row(
                mode="auto",
                status="blocked",
                scope="whole_site",
                triggered_by="admin:ops@example.com",
                updated_at=datetime.now(UTC) - timedelta(hours=5),
            )
        )
        result = await apply_judgment(
            conn, _settings(), _judgment(severity=20, direction="deescalation"), news_event_id=7
        )
        self.assertEqual(result["action"], "none")
        self.assertEqual(conn.executed, [])


if __name__ == "__main__":
    unittest.main()
