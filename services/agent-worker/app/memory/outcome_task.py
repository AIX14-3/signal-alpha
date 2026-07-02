"""Queue handler that runs the episodic-memory outcome recorder (Wave 3 후속②).

A periodic maintenance task (seeded daily by the drain daemon): it does not
belong to any single stock — it sweeps *all* matured, not-yet-complete episodes
and merges their realized forward returns into ``signal_episodes.outcome``. Fully
degrade-safe: disabled by config → no-op; per-episode failures are swallowed by
the recorder and retried next cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.memory.episode_outcome import EpisodeOutcomeRecorder


class EpisodeOutcomeTaskHandler:
    def __init__(self, connection: Any, *, settings: Any) -> None:
        self._connection = connection
        self._settings = settings

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        settings = self._settings
        if not getattr(settings, "episode_outcome_enabled", True):
            return {"status": "disabled", "scanned": 0, "recorded": 0, "skipped": 0}

        from signal_alpha_data_access.repositories import (
            MarketDataRepository,
            SignalEpisodeRepository,
        )

        recorder = EpisodeOutcomeRecorder(
            episodes=SignalEpisodeRepository(self._connection),
            market_data=MarketDataRepository(self._connection),
            horizons=list(getattr(settings, "episode_outcome_horizons", [5, 20, 60])),
            primary_days=int(getattr(settings, "episode_outcome_primary_days", 20)),
        )
        summary = await recorder.record_due(
            limit=int(getattr(settings, "episode_outcome_batch_limit", 500))
        )
        return {"status": "ok", **summary}
