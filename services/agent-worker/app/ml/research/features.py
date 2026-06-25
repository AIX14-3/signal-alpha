"""Feature adapter: analyzer indicators (+ existing rule score) -> ML feature row.

Per the plan, the 1st-layer (per-source) model is fed the ENGINEERED indicators
that ``analyzers/*/indicators.py`` already compute, plus the current tanh rule
score as one extra feature (it encodes domain weights — a useful prior). We do
NOT invent a new preprocessing stage; we reuse what exists.

The adapter is intentionally dependency-free and shape-agnostic: it accepts an
indicator dataclass (e.g. ``HiringIndicators``) or a plain dict, flattens the
numeric fields, prefixes them by source to avoid collisions, and drops
non-numeric/identifier fields. Missing values become ``nan`` so downstream
imputation can handle them uniformly.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Iterable, Mapping

# Indicator fields that are identifiers/strings, not learnable signal.
_NON_FEATURE_KEYS = frozenset(
    {
        "latest_observed_date",
        "polarity",
        "polarity_source",
        "polarity_model",
        "keyword",
        "keyword_group",
    }
)


def _to_mapping(indicators: Any) -> Mapping[str, Any]:
    if dataclasses.is_dataclass(indicators) and not isinstance(indicators, type):
        return dataclasses.asdict(indicators)
    if isinstance(indicators, Mapping):
        return indicators
    raise TypeError(f"unsupported indicators type: {type(indicators)!r}")


def _coerce_float(value: Any) -> float:
    """Map a value to float, turning ``None``/bools/garbage into a usable number.

    ``None`` -> nan (treated as missing). Booleans -> 1.0/0.0 (e.g. is_spike flags).
    Non-coercible values -> nan rather than raising, so one odd field can't sink a
    whole training run.
    """
    if value is None:
        return math.nan
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def build_feature_row(
    source: str,
    indicators: Any,
    *,
    rule_score: float | None = None,
) -> dict[str, float]:
    """Flatten one source's indicators into ``{feature_name: float}``.

    Keys are prefixed with ``source`` (e.g. ``hiring__momentum_pct``) so multiple
    sources can be merged into one row without name clashes. The existing rule
    score, when provided, is added as ``<source>__rule_score``.
    """
    mapping = _to_mapping(indicators)
    row: dict[str, float] = {}
    for key, value in mapping.items():
        if key in _NON_FEATURE_KEYS:
            continue
        row[f"{source}__{key}"] = _coerce_float(value)
    if rule_score is not None:
        row[f"{source}__rule_score"] = _coerce_float(rule_score)
    return row


def feature_matrix(
    rows: Iterable[Mapping[str, float]],
) -> tuple[list[list[float]], list[str]]:
    """Align a sequence of sparse feature dicts into a dense matrix.

    Returns ``(X, feature_names)`` where ``feature_names`` is the sorted union of
    all keys and every row is filled to that schema (missing -> nan). Sorting keeps
    column order stable/reproducible across runs.
    """
    rows = list(rows)
    names = sorted({key for row in rows for key in row})
    matrix = [[_coerce_float(row.get(name)) for name in names] for row in rows]
    return matrix, names
