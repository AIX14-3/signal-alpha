"""Recall cosine-nearest past episodes for the orchestrator's judge.

The recall result is a *reference* the judge may surface in reasoning /
needs_review calibration. It never touches the headline number. Like the writer,
every failure degrades to an empty list so publishing proceeds memory-free.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.memory.situation import SituationInput, build_situation_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecalledEpisode:
    stock_id: int | None
    signal_date: Any
    run_key: str | None
    direction: str | None
    score: float | None
    sources: Any | None
    outcome: Any | None
    distance: float | None

    @property
    def similarity(self) -> float | None:
        """Cosine similarity (1 - distance) for display; None if distance absent."""
        if self.distance is None:
            return None
        return round(1.0 - float(self.distance), 4)


class EpisodeRecall:
    def __init__(self, *, embedder: Any, repository: Any) -> None:
        self._embedder = embedder
        self._repository = repository

    async def recall(
        self,
        *,
        signal: str,
        breakdown: Mapping[str, Any],
        exclude_stock_id: int | None = None,
        exclude_signal_date: Any | None = None,
        limit: int = 3,
    ) -> list[RecalledEpisode]:
        """Embed the current situation and return the top-k nearest past episodes.

        Returns ``[]`` on cold start (no history) or any embedding/DB failure —
        the caller then judges without memory.
        """
        if self._embedder is None or self._repository is None:
            return []
        try:
            text = build_situation_text(SituationInput(signal=signal, breakdown=breakdown))
            embedding = await self._embedder.embed(text)
            rows = await self._repository.recall_similar(
                embedding=embedding,
                exclude_stock_id=exclude_stock_id,
                exclude_signal_date=exclude_signal_date,
                limit=limit,
            )
            return [_to_episode(dict(row)) for row in rows]
        except Exception as exc:  # noqa: BLE001 — memory is best-effort, never sink publish
            logger.warning(
                "에피소드 메모리 회상 실패 (stock_id=%s): %s — 메모리 없이 종합 계속",
                exclude_stock_id,
                exc,
            )
            return []


def _to_episode(row: dict[str, Any]) -> RecalledEpisode:
    return RecalledEpisode(
        stock_id=row.get("stock_id"),
        signal_date=row.get("signal_date"),
        run_key=row.get("run_key"),
        direction=row.get("direction"),
        score=row.get("score"),
        sources=row.get("sources"),
        outcome=row.get("outcome"),
        distance=row.get("distance"),
    )
