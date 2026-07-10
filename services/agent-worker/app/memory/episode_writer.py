"""Write a published signal's situation into episodic memory.

Best-effort by contract: embedding or DB failure must never break publishing.
The caller (aggregation handler) invokes :meth:`EpisodeWriter.write` *after* the
final signal is persisted; a raised/degraded write is swallowed and logged.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.memory.situation import SituationInput, build_situation_text

logger = logging.getLogger(__name__)

# Directions that carry no directional stance — a "no-signal" episode. Remembering
# these is pointless (recall neighbors would be dominated by near-identical
# no-signal days) and wasteful (every one still costs an embedding call), so the
# writer skips them entirely. Only positive/negative/mixed situations are stored.
_NO_SIGNAL_DIRECTIONS = frozenset({"", "neutral", "no_signal", "none", "unknown"})


def _is_no_signal(signal: str | None) -> bool:
    return str(signal or "").strip().lower() in _NO_SIGNAL_DIRECTIONS


class EpisodeWriter:
    def __init__(self, *, embedder: Any, repository: Any) -> None:
        # embedder: GeminiEmbeddingClient-like (``async embed(text) -> list[float]``).
        # repository: SignalEpisodeRepository-like (``async upsert_episode(...)``).
        self._embedder = embedder
        self._repository = repository

    async def write(
        self,
        *,
        stock_id: int,
        signal_date: Any,
        run_key: str,
        signal: str,
        final_score: float | None,
        breakdown: Mapping[str, Any],
        sources: Any | None = None,
        situation_signal: str | None = None,
    ) -> bool:
        """Embed the situation and upsert one episode. Returns True on success.

        ``situation_signal`` is what goes into the *embedding text* and MUST match
        what recall uses (the deterministic ``aggregate["signal"]``, the only value
        available at both write- and recall-time) — otherwise the cosine neighbors
        are meaningless. ``signal``/``final_score`` are the *published* headline,
        stored as the episode's ``direction``/``score`` for later outcome analysis.

        Degrades to False (no raise) on any embedding/DB error so the publish path
        is unaffected — episodic memory is an optional reference layer.
        """
        if self._embedder is None or self._repository is None:
            return False
        # No-signal situations are not worth remembering — skip before embedding so
        # a batch replay over historical no-signal days neither pollutes recall nor
        # burns embedding calls. The headline is still published; only memory skips.
        if _is_no_signal(signal):
            return False
        try:
            text = build_situation_text(
                SituationInput(signal=situation_signal or signal, breakdown=breakdown)
            )
            embedding = await self._embedder.embed(text)
            await self._repository.upsert_episode(
                stock_id=stock_id,
                signal_date=signal_date,
                run_key=run_key,
                embedding=embedding,
                direction=signal,
                score=final_score,
                sources=sources if sources is not None else _sources_summary(breakdown),
            )
            return True
        except Exception as exc:  # noqa: BLE001 — memory is best-effort, never sink publish
            logger.warning(
                "에피소드 메모리 기록 실패 (stock_id=%s, date=%s): %s — 메모리 없이 발행 계속",
                stock_id,
                signal_date,
                exc,
            )
            return False


def _sources_summary(breakdown: Mapping[str, Any]) -> dict[str, Any]:
    """Compact per-source direction/score snapshot stored beside the embedding.

    Only sources that actually ran are recorded (missing sources are dropped), so
    ``sources`` reflects the real firing set of the episode.
    """
    summary: dict[str, Any] = {}
    for source, entry in breakdown.items():
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("data_status") or "") in ("", "missing"):
            continue
        summary[source] = {
            "direction": entry.get("direction"),
            "score": entry.get("score"),
            "data_status": entry.get("data_status"),
        }
    return summary
