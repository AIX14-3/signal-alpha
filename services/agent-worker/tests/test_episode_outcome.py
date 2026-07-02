"""Episode outcome recorder (Wave-3 후속②) — realized forward-return recording.

Covers:
  - multi-horizon fwd_return + direction-hit math (positive/negative),
  - **trading-day** (not calendar-day) horizon alignment,
  - progressive fill (only matured horizons; already-recorded horizon skipped),
  - degrade (missing price → skip, retried next cycle),
  - finalize only when all horizons present; delayed price does NOT finalize,
  - abandoned finalize once the calendar grace has elapsed with prices missing,
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
    """In-memory ohlcv session series exposing ``list_sessions_from``.

    ``series`` is an iterable of (date, close). Each entry is one trading session;
    ``list_sessions_from`` returns them oldest-first so index == trading-day offset
    from the base session (adjusted_close absent → recorder falls back to close).
    """

    def __init__(self, series):
        self.series = sorted(series, key=lambda item: item[0])

    async def list_sessions_from(self, *, stock_id, from_date, limit):
        target = from_date if isinstance(from_date, date) else date.fromisoformat(str(from_date)[:10])
        rows = [
            {"trade_date": day, "close": close, "adjusted_close": None}
            for day, close in self.series
            if day >= target
        ]
        return rows[: int(limit)]


class FakeEpisodesRepo:
    def __init__(self, rows):
        self.rows = list(rows)
        self.patches = {}
        self.requested_min_horizon = None

    async def list_pending_outcomes(self, *, min_horizon_days, limit=500):
        # Mirror the real SQL filter: exclude episodes already flagged complete.
        import json

        self.requested_min_horizon = min_horizon_days
        pending = []
        for row in self.rows:
            oc = row.get("outcome")
            if isinstance(oc, str):
                try:
                    oc = json.loads(oc)
                except (ValueError, TypeError):
                    oc = None
            if isinstance(oc, dict) and str(oc.get("complete", "")).lower() == "true":
                continue
            pending.append(row)
        return pending[: int(limit)]

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


def _daily_sessions(start, count, prices=None):
    """`count` consecutive daily sessions from `start`; index i price = prices.get(i, 100)."""
    prices = prices or {}
    return [(start + timedelta(days=i), float(prices.get(i, 100.0))) for i in range(count)]


def _business_sessions(start, count, prices=None):
    """`count` consecutive *weekday* sessions from `start` (skips Sat/Sun)."""
    prices = prices or {}
    out = []
    day = start
    i = 0
    while len(out) < count:
        if day.weekday() < 5:
            out.append((day, float(prices.get(i, 100.0))))
            i += 1
        day += timedelta(days=1)
    return out


class RecorderMathTest(unittest.IsolatedAsyncioTestCase):
    async def test_multi_horizon_fwd_return_and_hit(self):
        sig = date.today() - timedelta(days=120)
        # 61 consecutive sessions: base(0)=100, idx5=105 (+5%), idx20=90 (-10%), idx60=130 (+30%).
        market = FakeMarketData(_daily_sessions(sig, 61, {5: 105.0, 20: 90.0, 60: 130.0}))
        repo = FakeEpisodesRepo([_episode(direction="positive", days_ago=120)])
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
        self.assertNotIn("abandoned", patch)
        self.assertIn("computed_at", patch)

    async def test_horizon_is_trading_days_not_calendar(self):
        # Weekday-only sessions: the 20th trading session is >20 calendar days out,
        # so a calendar-day lookup (old behaviour) would pick a different bar.
        sig = date.today() - timedelta(days=120)
        sessions = _business_sessions(sig, 21, {0: 100.0, 20: 120.0})
        market = FakeMarketData(sessions)
        repo = FakeEpisodesRepo([_episode(direction="positive", days_ago=120)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[20], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertAlmostEqual(patch["h20"]["fwd_return"], 0.20, places=6)
        base_date = date.fromisoformat(patch["h20"]["base_date"])
        fwd_date = date.fromisoformat(patch["h20"]["fwd_date"])
        self.assertEqual(base_date, sessions[0][0])
        self.assertEqual(fwd_date, sessions[20][0])
        # 20 trading sessions span more than 20 calendar days (weekends) — proves
        # the window is trading-day, not calendar-day (timedelta(days=20)) based.
        self.assertGreater((fwd_date - base_date).days, 20)

    async def test_negative_direction_hit_when_price_falls(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData(_daily_sessions(sig, 6, {0: 100.0, 5: 80.0}))
        repo = FakeEpisodesRepo([_episode(direction="negative")])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5], primary_days=5
        )
        await recorder.record_due()
        self.assertTrue(repo.patches[1]["h5"]["hit"])       # negative dir, price down → hit

    async def test_neutral_direction_hit_is_none(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData(_daily_sessions(sig, 6, {0: 100.0, 5: 110.0}))
        repo = FakeEpisodesRepo([_episode(direction="neutral")])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5], primary_days=5
        )
        await recorder.record_due()
        self.assertIsNone(repo.patches[1]["h5"]["hit"])


class ProgressiveAndIdempotentTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_matured_horizons_filled(self):
        # Only 6 sessions exist (idx 0..5): h5 matures; h20/h60 have no session yet.
        sig = date.today() - timedelta(days=10)
        market = FakeMarketData(_daily_sessions(sig, 6, {0: 100.0, 5: 103.0}))
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
        sig = date.today() - timedelta(days=120)
        market = FakeMarketData(_daily_sessions(sig, 61, {5: 105.0, 20: 120.0, 60: 130.0}))
        prior = {"h5": {"fwd_return": 0.01, "hit": True, "horizon_days": 5}}
        repo = FakeEpisodesRepo([_episode(outcome=prior, days_ago=120)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertNotIn("h5", patch)   # already present → not recomputed
        self.assertIn("h20", patch)
        self.assertIn("h60", patch)
        self.assertTrue(patch["complete"])
        self.assertNotIn("abandoned", patch)   # all horizons present → normal finalize

    async def test_outcome_as_json_string_is_parsed(self):
        sig = date.today() - timedelta(days=70)
        market = FakeMarketData(_daily_sessions(sig, 6, {0: 100.0, 5: 105.0}))
        repo = FakeEpisodesRepo([_episode(outcome='{"h5": {"fwd_return": 0.02}}', days_ago=70)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5], primary_days=5
        )
        await recorder.record_due()
        # h5 already present in the stringified outcome → nothing new, but all
        # horizons now present → finalize with complete flag only.
        patch = repo.patches[1]
        self.assertNotIn("h5", patch)
        self.assertTrue(patch["complete"])
        self.assertNotIn("abandoned", patch)


class DegradeTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_forward_price_skips_without_writing(self):
        # Only the base session exists: h5 has no forward session yet, within grace.
        sig = date.today() - timedelta(days=3)
        market = FakeMarketData(_daily_sessions(sig, 1, {0: 100.0}))
        repo = FakeEpisodesRepo([_episode(days_ago=3)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        summary = await recorder.record_due()
        self.assertEqual(summary["recorded"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(repo.patches, {})   # nothing written → retried next cycle

    async def test_price_delay_within_grace_does_not_finalize(self):
        # 100 days old (past 60d calendar, but well within the ~210d abandon grace)
        # with NO price at all → must NOT finalize; retried so a late price is kept.
        repo = FakeEpisodesRepo([_episode(days_ago=100)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=FakeMarketData([]), horizons=[5, 20, 60], primary_days=20
        )
        summary = await recorder.record_due()
        self.assertEqual(summary["recorded"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(repo.patches, {})   # not finalized — no premature complete

    async def test_partial_prices_within_grace_records_but_not_complete(self):
        # h5 price present but h20/h60 delayed, still within grace → record h5 only,
        # do NOT finalize (delayed longer horizons must not be lost).
        sig = date.today() - timedelta(days=100)
        market = FakeMarketData(_daily_sessions(sig, 6, {0: 100.0, 5: 108.0}))
        repo = FakeEpisodesRepo([_episode(days_ago=100)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertIn("h5", patch)
        self.assertNotIn("complete", patch)

    async def test_grace_expired_without_price_finalizes_abandoned(self):
        # Grace = 60*2+90 = 210 calendar days. 300 days old with no price at all →
        # abandon: complete + abandoned so the episode stops being re-scanned.
        repo = FakeEpisodesRepo([_episode(days_ago=300)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=FakeMarketData([]), horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        patch = repo.patches[1]
        self.assertTrue(patch["complete"])
        self.assertTrue(patch["abandoned"])
        self.assertNotIn("h5", patch)

    async def test_recompute_is_deterministic(self):
        # Re-running against the same matured data (outcome not yet persisted) must
        # recompute byte-identical horizon values — merge_outcome `||` is idempotent.
        sig = date.today() - timedelta(days=120)
        market = FakeMarketData(_daily_sessions(sig, 61, {5: 105.0, 20: 90.0, 60: 130.0}))
        repo = FakeEpisodesRepo([_episode(direction="positive", days_ago=120)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        first = {k: v for k, v in repo.patches[1].items() if k != "computed_at"}
        await recorder.record_due()
        second = {k: v for k, v in repo.patches[1].items() if k != "computed_at"}
        self.assertEqual(first, second)

    async def test_completed_episode_is_not_rescanned(self):
        # Once finalized, persisting the merged outcome back onto the row removes it
        # from the pending scan entirely (no re-work, no drift).
        sig = date.today() - timedelta(days=120)
        market = FakeMarketData(_daily_sessions(sig, 61, {5: 105.0, 20: 90.0, 60: 130.0}))
        repo = FakeEpisodesRepo([_episode(direction="positive", days_ago=120)])
        recorder = EpisodeOutcomeRecorder(
            episodes=repo, market_data=market, horizons=[5, 20, 60], primary_days=20
        )
        await recorder.record_due()
        self.assertTrue(repo.patches[1]["complete"])
        repo.rows[0]["outcome"] = repo.patches[1]   # simulate jsonb persistence
        summary = await recorder.record_due()
        self.assertEqual(summary, {"scanned": 0, "recorded": 0, "skipped": 0})

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
