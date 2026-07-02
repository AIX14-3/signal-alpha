"""Episode outcome recorder (Wave-3 후속②) — realized forward-return recording.

Covers:
  - multi-horizon fwd_return + direction-hit math (positive/negative),
  - progressive fill (only matured horizons; already-recorded horizon skipped),
  - degrade (missing price → skip, retried next cycle),
  - completion flag (all horizons present, or longest window matured),
  - jsonb string outcome parsing, and the queue handler disabled no-op.

Numbers stay recall-only: the recorder writes signal_episodes.outcome and never
touches any headline score/direction (invariant carried over from #728).
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.memory import EpisodeOutcomeRecorder, EpisodeOutcomeTaskHandler


class FakeMarketData:
    """In-memory ohlcv: get_price_on_or_after returns the first session on/after a date."""

    def __init__(self, series):
        # series: iterable of (date, close). adjusted_close absent → falls back to close.
        self.series = sorted(series, key=lambda item: item[0])

    async def get_price_on_or_after(self, *, stock_id, trade_date):
        target = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date)[:10])
        for day, close in self.series:
            if day >= target:
                return {"trade_date": day, "close": close, "adjusted_close": None}
        return None


class FakeEpisodesRepo:
    def __init__(self, rows):
        self.rows = list(rows)
        self.patches = {}
        self.requested_min_horizon = None

    async def list_pending_outcomes(self, *, min_horizon_days, limit=500):
        self.requested_min_horizon = min_horizon_days
        return list(self.rows)

    async def merge_outcome(self, *, episode_id, patch):
        # emulate jsonb `||`: shallow-merge onto any prior patch for the same id.
        merged = dict(self.patches.get(episode_id, {}))
        merged.update(patch)
        self.patches[episode_id] = merged
        return {"id": episode_id}


def _episode(stock_id=1, days_ago=70, direction="positive", outcome=None, episode_id=1):
    return {
        "id": episode_id,
        "stock_id": stock_id,
        "signal_date": date.today() - timedelta(days=days_ago),
        "run_key": "AGGREGATED",
        "direction": direction,
        "score": 60.0,
        "outcome": outcome,
    }


class RecorderMathTest(unittest.IsolatedAsyncioTestCase):
    async def test_multi_horizon_fwd_return_and_hit(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData([
            (sig, 100.0),
            (sig + timedelta(days=5), 105.0),   # +5% up
            (sig + timedelta(days=20), 90.0),   # -10% down
            (sig + timedelta(days=60), 130.0),  # +30% up
        ])
        repo = FakeEpisodesRepo([_episode(direction="positive")])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        summary = await recorder.record_due(limit=10)

        self.assertEqual(summary, {"scanned": 1, "recorded": 1, "skipped": 0})
        self.assertEqual(repo.requested_min_horizon, 5)  # shortest horizon gates the scan
        patch = repo.patches[1]
        self.assertAlmostEqual(patch["h5"]["fwd_return"], 0.05, places=6)
        self.assertTrue(patch["h5"]["hit"])                 # positive dir, price up
        self.assertAlmostEqual(patch["h20"]["fwd_return"], -0.10, places=6)
        self.assertFalse(patch["h20"]["hit"])               # positive dir, price down
        self.assertTrue(patch["h60"]["hit"])
        self.assertEqual(patch["h20"]["realized_direction"], "negative")
        self.assertEqual(patch["primary"], "h20")
        self.assertTrue(patch["complete"])                  # all horizons present
        self.assertIn("computed_at", patch)

    async def test_negative_direction_hit_when_price_falls(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData([(sig, 100.0), (sig + timedelta(days=5), 80.0)])
        repo = FakeEpisodesRepo([_episode(direction="negative")])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5], primary_days=5
        )
        await recorder.record_due()
        self.assertTrue(repo.patches[1]["h5"]["hit"])       # negative dir, price down → hit

    async def test_neutral_direction_hit_is_none(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData([(sig, 100.0), (sig + timedelta(days=5), 110.0)])
        repo = FakeEpisodesRepo([_episode(direction="neutral")])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5], primary_days=5
        )
        await recorder.record_due()
        self.assertIsNone(repo.patches[1]["h5"]["hit"])


class ProgressiveAndIdempotentTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_matured_horizons_filled(self):
        # 10 days old: only h5 matured; h20/h60 windows still open.
        sig = date.today() - timedelta(days=10)
        market = FakeMarketData([(sig, 100.0), (sig + timedelta(days=5), 103.0)])
        repo = FakeEpisodesRepo([_episode(days_ago=10)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertIn("h5", patch)
        self.assertNotIn("h20", patch)
        self.assertNotIn("h60", patch)
        self.assertNotIn("complete", patch)  # not finalized — retry for h20/h60 later

    async def test_already_recorded_horizon_is_skipped(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData([
            (sig, 100.0),
            (sig + timedelta(days=5), 105.0),
            (sig + timedelta(days=20), 120.0),
            (sig + timedelta(days=60), 130.0),
        ])
        prior = {"h5": {"fwd_return": 0.01, "hit": True, "horizon_days": 5}}
        repo = FakeEpisodesRepo([_episode(outcome=prior)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertNotIn("h5", patch)   # already present → not recomputed
        self.assertIn("h20", patch)
        self.assertIn("h60", patch)
        self.assertTrue(patch["complete"])

    async def test_outcome_as_json_string_is_parsed(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData([(sig, 100.0), (sig + timedelta(days=5), 105.0)])
        repo = FakeEpisodesRepo([_episode(outcome='{"h5": {"fwd_return": 0.02}}', days_ago=70)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5], primary_days=5
        )
        await recorder.record_due()
        # h5 already present in the stringified outcome → nothing new, but longest
        # window matured → finalize with complete flag only.
        patch = repo.patches[1]
        self.assertNotIn("h5", patch)
        self.assertTrue(patch["complete"])


class DegradeTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_forward_price_skips_without_writing(self):
        # 3 days old: h5 window not matured yet; base present, no forward price.
        sig = date.today() - timedelta(days=3)
        market = FakeMarketData([(sig, 100.0)])
        repo = FakeEpisodesRepo([_episode(days_ago=3)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        summary = await recorder.record_due()
        self.assertEqual(summary["recorded"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(repo.patches, {})   # nothing written → retried next cycle

    async def test_matured_but_no_price_finalizes_to_stop_rescan(self):
        # 70 days old (longest window matured) but no price at all → finalize with
        # complete flag so the episode stops being re-scanned forever.
        repo = FakeEpisodesRepo([_episode(days_ago=70)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=FakeMarketData([]), horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertTrue(patch["complete"])
        self.assertNotIn("h5", patch)

    async def test_no_horizons_is_noop(self):
        repo = FakeEpisodesRepo([_episode()])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=FakeMarketData([]), horizons=[], primary_days=20
        )
        summary = await recorder.record_due()
        self.assertEqual(summary, {"scanned": 0, "recorded": 0, "skipped": 0})


class _FakeSettings:
    def __init__(self, enabled):
        self.episode_outcome_enabled = enabled
        self.episode_outcome_horizons = [5, 20, 60]
        self.episode_outcome_primary_days = 20
        self.episode_outcome_batch_limit = 500


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_is_noop(self):
        # connection is never touched when disabled (would raise on attribute access).
        handler = EpisodeOutcomeTaskHandler(object(), settings=_FakeSettings(enabled=False))
        result = await handler({"id": 1})
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["recorded"], 0)


if __name__ == "__main__":
    unittest.main()
