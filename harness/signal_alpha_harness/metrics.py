"""The three evaluation metrics plus the permutation-test acceptance gate.

All cross-sectional math runs on date×ticker matrices so the 500-shuffle
permutation test stays fast. Scores are shuffled *within each date* — that
preserves every property of the return panel and of the score distribution per
day, so the null is exactly "this score has no cross-sectional information".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

POSITIVE_THRESHOLD = 0.2
NEGATIVE_THRESHOLD = -0.2
MIN_NAMES_PER_DAY = 5
QUANTILES = 5


@dataclass(frozen=True)
class MetricsReport:
    horizon: int
    n_days: int
    n_observations: int
    hit_rate: float | None
    n_directional: int
    mean_ic: float | None
    ic_positive_share: float | None
    quantile_spread: float | None


def _matrices(frame: pd.DataFrame, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """Pivot to aligned date×ticker score / forward-return matrices (NaN-padded)."""
    scores = frame.pivot_table(index="trade_date", columns="ticker", values="score", aggfunc="first")
    returns = frame.pivot_table(
        index="trade_date", columns="ticker", values=f"fwd_ret_{horizon}", aggfunc="first"
    )
    returns = returns.reindex(index=scores.index, columns=scores.columns)
    return scores.to_numpy(dtype=float), returns.to_numpy(dtype=float)


def _rank_rows(matrix: np.ndarray) -> np.ndarray:
    """Average-free ordinal ranks per row, NaN-preserving (ties broken by order)."""
    ranks = np.full(matrix.shape, np.nan)
    for row_index in range(matrix.shape[0]):
        row = matrix[row_index]
        mask = ~np.isnan(row)
        if mask.sum() == 0:
            continue
        order = np.argsort(row[mask], kind="stable")
        row_ranks = np.empty(mask.sum(), dtype=float)
        row_ranks[order] = np.arange(1, mask.sum() + 1, dtype=float)
        ranks[row_index, mask] = row_ranks
    return ranks


def daily_spearman_ic(scores: np.ndarray, returns: np.ndarray) -> np.ndarray:
    """Per-day Spearman IC; NaN for days with fewer than MIN_NAMES_PER_DAY pairs."""
    joint_mask = ~np.isnan(scores) & ~np.isnan(returns)
    masked_scores = np.where(joint_mask, scores, np.nan)
    masked_returns = np.where(joint_mask, returns, np.nan)
    score_ranks = _rank_rows(masked_scores)
    return_ranks = _rank_rows(masked_returns)

    ics = np.full(scores.shape[0], np.nan)
    for row_index in range(scores.shape[0]):
        mask = joint_mask[row_index]
        if mask.sum() < MIN_NAMES_PER_DAY:
            continue
        a = score_ranks[row_index, mask]
        b = return_ranks[row_index, mask]
        a_std, b_std = a.std(), b.std()
        if a_std == 0 or b_std == 0:
            continue
        ics[row_index] = float(np.corrcoef(a, b)[0, 1])
    return ics


def direction_hit_rate(
    frame: pd.DataFrame,
    horizon: int,
    *,
    positive_threshold: float = POSITIVE_THRESHOLD,
    negative_threshold: float = NEGATIVE_THRESHOLD,
) -> tuple[float | None, int]:
    """Hit rate over positive/negative calls only (neutral/unknown excluded).

    기본 임계값(±0.2)은 [-1,1] 점수용 — 0~100 백분위 점수는 호출자가
    (예: 80/20)으로 넘겨야 의미가 있다.
    """
    returns = frame[f"fwd_ret_{horizon}"]
    positive = frame["score"] >= positive_threshold
    negative = frame["score"] <= negative_threshold
    directional = (positive | negative) & returns.notna()
    n_directional = int(directional.sum())
    if n_directional == 0:
        return None, 0
    hits = (positive & (returns > 0)) | (negative & (returns < 0))
    return float(hits[directional].mean()), n_directional


def quantile_spread(scores: np.ndarray, returns: np.ndarray) -> float | None:
    """Mean (top-quantile − bottom-quantile) forward return across days."""
    spreads: list[float] = []
    for row_index in range(scores.shape[0]):
        mask = ~np.isnan(scores[row_index]) & ~np.isnan(returns[row_index])
        if mask.sum() < QUANTILES * 2:
            continue
        day_scores = scores[row_index, mask]
        day_returns = returns[row_index, mask]
        order = np.argsort(day_scores, kind="stable")
        bucket = max(1, len(order) // QUANTILES)
        bottom = day_returns[order[:bucket]].mean()
        top = day_returns[order[-bucket:]].mean()
        spreads.append(float(top - bottom))
    if not spreads:
        return None
    return float(np.mean(spreads))


def compute_metrics(
    frame: pd.DataFrame,
    horizon: int,
    *,
    positive_threshold: float = POSITIVE_THRESHOLD,
    negative_threshold: float = NEGATIVE_THRESHOLD,
) -> MetricsReport:
    scores, returns = _matrices(frame, horizon)
    ics = daily_spearman_ic(scores, returns)
    valid_ics = ics[~np.isnan(ics)]
    hit, n_directional = direction_hit_rate(
        frame,
        horizon,
        positive_threshold=positive_threshold,
        negative_threshold=negative_threshold,
    )
    observed = frame[frame["score"].notna() & frame[f"fwd_ret_{horizon}"].notna()]
    return MetricsReport(
        horizon=horizon,
        n_days=int(scores.shape[0]),
        n_observations=int(len(observed)),
        hit_rate=hit,
        n_directional=n_directional,
        mean_ic=float(valid_ics.mean()) if len(valid_ics) else None,
        ic_positive_share=float((valid_ics > 0).mean()) if len(valid_ics) else None,
        quantile_spread=quantile_spread(scores, returns),
    )


def permutation_pvalue(
    frame: pd.DataFrame,
    horizon: int,
    *,
    n_permutations: int = 500,
    seed: int = 42,
) -> float | None:
    """p-value for mean IC under whole-ticker (column) score shuffling.

    One-sided: how often does a shuffled score panel reach the observed mean IC?

    Each replicate permutes ticker identities once and applies it to the whole
    score history. Shuffling within each date independently is anti-conservative
    here: forward returns overlap across dates, so daily ICs are autocorrelated,
    and a per-date shuffle destroys that correlation in the null — the harness
    self-check (무신호 시나리오) caught exactly that false-discovery mode.
    Column permutation keeps the score's time-series structure in the null and
    only breaks the score↔ticker alignment, which is the hypothesis under test.
    """
    scores, returns = _matrices(frame, horizon)
    observed_ics = daily_spearman_ic(scores, returns)
    observed_valid = observed_ics[~np.isnan(observed_ics)]
    if len(observed_valid) == 0:
        return None
    observed_mean = observed_valid.mean()

    rng = np.random.default_rng(seed)
    n_columns = scores.shape[1]
    hits = 0
    for _ in range(n_permutations):
        shuffled = scores[:, rng.permutation(n_columns)]
        null_ics = daily_spearman_ic(shuffled, returns)
        null_valid = null_ics[~np.isnan(null_ics)]
        if len(null_valid) and null_valid.mean() >= observed_mean:
            hits += 1
    return (hits + 1) / (n_permutations + 1)
