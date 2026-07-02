"""Post-hoc realized-outcome recorder for episodic memory (Wave 3 후속②).

``EpisodeWriter`` stores an episode's *situation* at publish time but leaves
``signal_episodes.outcome`` NULL — we don't know yet whether that direction paid
off. This recorder runs *after the fact*: for episodes whose forward window has
matured, it computes the realized **forward return** over one or more horizons,
whether the published direction *hit*, and merges that into ``outcome`` so
``EpisodeRecall`` can later prefer "past situations that actually came true".

Design (per coordinator):
  - **Multi-horizon, progressive.** We record 5/20/60 **trading-day** windows
    (configurable). We pull the base session and the following trading sessions in
    one query (``list_sessions_from``); the base is index 0 and horizon ``h`` is
    index ``h``, so ``fwd_return`` matures over exactly ``h`` *trading* sessions —
    aligned with the production meta-learner return target (``fwd_return_20d``),
    not a calendar-day approximation. Each horizon is filled only once that many
    trading sessions exist, and merged in via ``merge_outcome`` (jsonb ``||``) so
    short horizons land first and 60d fills in weeks later — no re-backfill. The
    canonical/primary horizon is 20d.
  - **Numbers are immutable.** ``outcome`` is a post-hoc, recall-only reference.
    It never feeds the meta-learner direction/score (invariant from #728).
  - **Degrade / idempotent.** Missing price → skip that horizon (retried next
    cycle). Already-recorded horizon → skipped.
  - **Finalize only when actually done.** ``complete`` is set once the base *and*
    every horizon's forward price are recorded (all present). If prices are merely
    delayed we do **not** finalize — the episode is retried next cycle so a
    late-arriving price is never lost. Only after a generous calendar grace has
    elapsed with prices still missing (delisted / permanently absent) is the
    episode abandoned (``complete`` + ``abandoned``) to stop endless re-scans.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DIRECTIONAL = ("positive", "negative")


class EpisodeOutcomeRecorder:
    def __init__(
        self,
        *,
        episodes: Any,
        market_data: Any,
        horizons: list[int],
        primary_days: int = 20,
    ) -> None:
        # episodes: SignalEpisodeRepository-like (list_pending_outcomes / merge_outcome).
        # market_data: MarketDataRepository-like (get_price_on_or_after).
        self._episodes = episodes
        self._market_data = market_data
        # de-dup + sort so maturity/scan bounds use the true min/max horizon.
        self._horizons = sorted({int(h) for h in horizons if int(h) > 0})
        self._primary_key = f"h{int(primary_days)}"
        # Calendar grace before an episode with missing prices is abandoned. The
        # longest horizon is in *trading* days (~1.4 calendar days each); double it
        # and pad 90 days so genuinely-delayed prices are always filled first, and
        # only truly delisted / permanently-absent episodes are ever abandoned.
        self._abandon_after_days = (self._horizons[-1] * 2 + 90) if self._horizons else 0

    async def record_due(self, *, limit: int = 500) -> dict[str, Any]:
        """Sweep matured, not-yet-complete episodes and fill their realized outcomes."""
        if not self._horizons:
            return {"scanned": 0, "recorded": 0, "skipped": 0}
        rows = await self._episodes.list_pending_outcomes(
            min_horizon_days=self._horizons[0], limit=limit
        )
        scanned = recorded = skipped = 0
        for row in rows:
            scanned += 1
            try:
                patch = await self._compute_patch(dict(row))
            except Exception as exc:  # noqa: BLE001 — one bad episode must not sink the sweep
                logger.warning(
                    "에피소드 outcome 계산 실패 (id=%s): %s — 건너뜀(다음 주기 재시도)",
                    row.get("id") if hasattr(row, "get") else None,
                    exc,
                )
                skipped += 1
                continue
            if patch is None:
                skipped += 1
                continue
            await self._episodes.merge_outcome(episode_id=int(row["id"]), patch=patch)
            recorded += 1
        return {"scanned": scanned, "recorded": recorded, "skipped": skipped}

    async def _compute_patch(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build the jsonb patch of newly-matured horizons for one episode, or None.

        Returns None when there is nothing new to write yet (window not matured or
        price not available), so the episode is retried on the next sweep.
        """
        stock_id = int(row["stock_id"])
        signal_date = _to_date(row.get("signal_date"))
        direction = _norm_direction(row.get("direction"))
        existing = _as_dict(row.get("outcome"))
        existing_horizons = {k for k in existing if _is_horizon_key(k)}
        today = date.today()

        # Pull the base session and the following trading sessions in one shot.
        # Index 0 = base (first session on/after signal_date); index h = the h-th
        # trading session after base → an exact trading-day horizon window.
        max_horizon = self._horizons[-1]
        sessions = await self._market_data.list_sessions_from(
            stock_id=stock_id, from_date=signal_date, limit=max_horizon + 1
        )

        new_entries: dict[str, Any] = {}
        base = sessions[0] if sessions else None
        base_close = _price(base)
        if base is not None and base_close and base_close > 0:
            for horizon in self._horizons:
                key = f"h{horizon}"
                if key in existing_horizons:
                    continue
                if horizon >= len(sessions):
                    continue  # not enough trading sessions yet → window not matured
                fwd = sessions[horizon]
                fwd_close = _price(fwd)
                if fwd is None or not fwd_close:
                    continue  # price not available yet → retry next cycle
                fwd_return = (fwd_close - base_close) / base_close
                realized = _realized_direction(fwd_return)
                new_entries[key] = {
                    "fwd_return": round(fwd_return, 6),
                    "hit": (realized == direction) if direction in _DIRECTIONAL else None,
                    "horizon_days": horizon,
                    "realized_direction": realized,
                    "base_close": float(base_close),
                    "fwd_close": float(fwd_close),
                    "base_date": _iso(_field(base, "trade_date")),
                    "fwd_date": _iso(_field(fwd, "trade_date")),
                }

        all_present = {f"h{h}" for h in self._horizons} <= (existing_horizons | set(new_entries))
        # Only abandon (finalize despite missing prices) once a generous calendar
        # grace has elapsed — until then a delayed price must not lock the episode.
        grace_expired = signal_date + timedelta(days=self._abandon_after_days) <= today
        abandoned = grace_expired and not all_present
        finalize = all_present or abandoned
        if not new_entries and not finalize:
            return None  # nothing matured/available yet — leave NULL, retry later

        patch: dict[str, Any] = dict(new_entries)
        patch["primary"] = self._primary_key
        patch["computed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Judge (#728) folds a recalled episode's *recorded outcome* into review
        # calibration by reading a top-level ``result``/``label`` tag (an adverse
        # tag — miss/false_positive — nudges needs_review). It does not understand
        # the per-horizon ``hit`` map, so derive that tag from the *primary*
        # horizon's hit to actually close the write→recall→judge loop. Only set
        # once the primary horizon is known (hit True/False); a not-yet-matured or
        # non-directional (hit=None) primary leaves it unset → cold-start safe.
        primary_entry = new_entries.get(self._primary_key) or existing.get(self._primary_key)
        primary_hit = primary_entry.get("hit") if isinstance(primary_entry, dict) else None
        if primary_hit is True:
            patch["result"] = "hit"
        elif primary_hit is False:
            patch["result"] = "miss"
        if finalize:
            # All horizons recorded (normal) or grace elapsed with prices still
            # missing (abandoned): either way stop re-scanning this episode.
            patch["complete"] = True
            if abandoned:
                patch["abandoned"] = True
        return patch


def _is_horizon_key(key: Any) -> bool:
    text = str(key)
    return len(text) > 1 and text[0] == "h" and text[1:].isdigit()


def _realized_direction(fwd_return: float) -> str:
    if fwd_return > 0:
        return "positive"
    if fwd_return < 0:
        return "negative"
    return "neutral"


def _norm_direction(value: Any) -> str:
    return str(value or "neutral").strip().lower()


def _field(row: Any, column: str) -> Any:
    """Read one column from a dict-like or asyncpg Record row (None if absent)."""
    if row is None:
        return None
    getter = row.get if hasattr(row, "get") else (lambda k: row[k])
    try:
        return getter(column)
    except (KeyError, IndexError):
        return None


def _price(row: Any) -> float | None:
    """Prefer adjusted close (splits/dividends), fall back to raw close."""
    if row is None:
        return None
    getter = row.get if hasattr(row, "get") else (lambda k: row[k])
    for column in ("adjusted_close", "close"):
        try:
            value = getter(column)
        except (KeyError, IndexError):
            value = None
        if value is not None:
            return float(value)
    return None


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
