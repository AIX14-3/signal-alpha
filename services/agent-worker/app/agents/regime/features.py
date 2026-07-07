"""Deterministic PIT market-regime features — NO LLM.

Channel (B) of the regime layer: the regime FEATURE that the meta-learner joins is
computed deterministically, point-in-time, from market/sector inputs. The LLM tag
(channel A) is display/audit only and is NEVER read back here — this keeps numbers
deterministic and PIT (mirrors the DataLab ML-boundary rule in
``app/agents/datalab/agent.py:174-178``).

The v1 proxy is cross-sectional **sector-return dispersion** as-of ``asof``: how
spread-out sector returns are on the latest available date. High dispersion is the
fingerprint of a sector-led wave (e.g. an AI-capex boom) — exactly the confound we
must control deterministically. LLM rationale can sit ON TOP of this number, never
replace it. The definition starts deterministic and is validated through the same
honest harness (BH/holdout/shuffle/within-firm+sector-neutral) before any belief.

Pure function over row dicts (no DB, unit-testable), same style as
``app/ml/source_features``. PIT gate: rows with ``date > asof`` are dropped.
"""

from __future__ import annotations

from datetime import date
from statistics import pstdev
from typing import Any, Mapping, Sequence

# The regime feature block keys — always present (None when undecidable), so the
# meta-learner's ``feature_order`` stays stable when the block is joined.
REGIME_FEATURE_KEYS: tuple[str, ...] = (
    "sector_return_dispersion",  # 최신일 섹터수익률 표준편차(쏠림 강도)
    "sector_return_spread",      # 최신일 섹터수익률 max-min
    "sector_return_mean",        # 최신일 섹터수익률 평균(광범위 방향성 프록시)
    "sector_count",              # 최신일 섹터 표본수(커버리지)
)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


def regime_features(
    asof: date,
    *,
    sector_return_rows: Sequence[Mapping[str, Any]] = (),
    date_key: str = "date",
    sector_key: str = "sector",
    return_key: str = "return",
) -> dict[str, float | None]:
    """Deterministic PIT regime proxies from per-sector daily returns.

    ``sector_return_rows``: dicts like ``{"date", "sector", "return"}``. Rows with a
    missing/unparseable date or ``date > asof`` are dropped (look-ahead 0). The
    feature is computed on the **latest available date** at/ before ``asof``. Returns
    all ``REGIME_FEATURE_KEYS`` (None when undecidable) so callers get a stable shape.
    """
    empty: dict[str, float | None] = {key: None for key in REGIME_FEATURE_KEYS}

    # PIT gate — drop future/undated rows (mirror source_features.pit_rows).
    kept: list[tuple[date, str, float]] = []
    for row in sector_return_rows:
        observed = _as_date(row.get(date_key))
        value = _as_float(row.get(return_key))
        sector = row.get(sector_key)
        if observed is None or observed > asof or value is None or sector is None:
            continue
        kept.append((observed, str(sector), value))
    if not kept:
        return empty

    latest = max(observed for observed, _, _ in kept)
    # One return per sector on the latest date (last write wins on dup sector).
    cross_section: dict[str, float] = {
        sector: value for observed, sector, value in kept if observed == latest
    }
    returns = list(cross_section.values())
    if not returns:
        return empty

    dispersion = pstdev(returns) if len(returns) > 1 else 0.0
    return {
        "sector_return_dispersion": dispersion,
        "sector_return_spread": max(returns) - min(returns),
        "sector_return_mean": sum(returns) / len(returns),
        "sector_count": float(len(returns)),
    }
