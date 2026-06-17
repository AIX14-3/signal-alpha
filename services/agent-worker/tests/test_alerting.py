"""Discord 알림 모듈(app/observability/alerting.py) 단위 테스트 (Phase 5)."""

from __future__ import annotations

import unittest
from unittest import mock

from app.observability.alerting import (
    _COLOR_FAILED,
    _COLOR_SILENT,
    build_run_alert_embed,
    send_discord_alert,
)
from app.observability.stats import RunStats


class BuildEmbedTest(unittest.TestCase):
    def _embed(self, status="failed", reason="전건 실패", **counts):
        stats = RunStats.from_counts(
            collected=counts.get("collected", 100),
            inserted=counts.get("inserted", 0),
            skipped=counts.get("skipped", 0),
            failed=counts.get("failed", 100),
        )
        return build_run_alert_embed("HIRING", stats, status, run_id=42, reason=reason)

    def test_fields_and_metadata(self):
        embed = self._embed()
        self.assertIn("HIRING", embed["title"])
        self.assertIn("전건 실패", embed["description"])
        self.assertIn("timestamp", embed)
        names = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(names["run_id"], "42")
        self.assertEqual(names["수집"], "100")
        self.assertEqual(names["실패"], "100")
        self.assertEqual(names["실패율"], "100.0%")

    def test_color_failed(self):
        self.assertEqual(self._embed(status="failed", reason="전건 실패")["color"], _COLOR_FAILED)

    def test_color_silent(self):
        embed = self._embed(status="success", reason="신규 적재 0건(침묵 실패 의심)",
                            collected=10, inserted=0, failed=0)
        self.assertEqual(embed["color"], _COLOR_SILENT)

    def test_ingest_rate_reflected(self):
        embed = self._embed(status="partial", reason="거부율 60.0% 초과",
                            collected=100, inserted=30, skipped=10, failed=60)
        names = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(names["적재성공률"], "40.0%")  # (30+10)/100
        self.assertEqual(names["실패율"], "60.0%")


class SendDiscordAlertTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_url_is_noop(self):
        http = mock.AsyncMock()
        result = await send_discord_alert(http, "", {"title": "x"})
        self.assertFalse(result)
        http.post.assert_not_called()

    async def test_posts_embeds_envelope(self):
        http = mock.AsyncMock()
        http.post.return_value = mock.Mock(raise_for_status=mock.Mock())
        result = await send_discord_alert(http, "https://discord.test/hook", {"title": "x"})
        self.assertTrue(result)
        _, kwargs = http.post.call_args
        self.assertEqual(kwargs["json"], {"embeds": [{"title": "x"}]})

    async def test_exception_is_swallowed(self):
        http = mock.AsyncMock()
        http.post.side_effect = RuntimeError("network down")
        result = await send_discord_alert(http, "https://discord.test/hook", {"title": "x"})
        self.assertFalse(result)  # 예외 삼킴 → 데몬 비중단


if __name__ == "__main__":
    unittest.main()
