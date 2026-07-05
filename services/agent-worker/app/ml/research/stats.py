"""Multiple-testing correction for the bake-off permutation tests.

We sweep many (model x horizon) permutation tests and must guard against false
positives from picking the best of many. Bonferroni (``0.05 / m``) is the only
correction the harness had; it ignores the strong correlation between our 16
models on the same data and so over-penalizes. Benjamini-Hochberg controls the
**false discovery rate** instead and is the appropriate gate for a correlated
family of confirmatory tests — we report raw p, Bonferroni, AND BH q so the call
is transparent.

Pure Python (no numpy/scipy) so the correction is trivially unit-testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BHResult:
    """Per-hypothesis Benjamini-Hochberg outcome, in the ORIGINAL input order."""

    rejected: list[bool]
    qvalues: list[float]
    n_rejected: int
    alpha: float


def benjamini_hochberg(pvalues: list[float], alpha: float = 0.05) -> BHResult:
    """Benjamini-Hochberg step-up FDR control at level ``alpha``.

    Returns rejection flags and BH-adjusted q-values aligned to the ORIGINAL
    order of ``pvalues``. A hypothesis is rejected iff its raw p is <= the BH
    threshold ``p_(k*)`` where ``k*`` is the largest rank with
    ``p_(k) <= (k/m) * alpha``.

    NaN / None p-values are treated as 1.0 (never rejected) so a degenerate
    model can't break the correction for the whole family.
    """
    m = len(pvalues)
    if m == 0:
        return BHResult(rejected=[], qvalues=[], n_rejected=0, alpha=alpha)

    clean = [
        1.0 if (p is None or (isinstance(p, float) and math.isnan(p))) else float(p)
        for p in pvalues
    ]
    # ascending order of p-values, remembering original positions
    order = sorted(range(m), key=lambda i: clean[i])

    # largest rank k (1-based) with p_(k) <= (k/m)*alpha  ->  reject ranks 1..k
    k_star = 0
    for rank, idx in enumerate(order, start=1):
        if clean[idx] <= (rank / m) * alpha:
            k_star = rank

    # BH-adjusted q-values: q_(k) = min over j>=k of (m/j)*p_(j), monotone, capped at 1
    q_sorted = [0.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        q = min(1.0, (m / rank) * clean[idx])
        running_min = min(running_min, q)
        q_sorted[rank - 1] = running_min

    rejected = [False] * m
    qvalues = [1.0] * m
    for rank, idx in enumerate(order, start=1):
        qvalues[idx] = q_sorted[rank - 1]
        if rank <= k_star:
            rejected[idx] = True

    return BHResult(
        rejected=rejected,
        qvalues=qvalues,
        n_rejected=k_star,
        alpha=alpha,
    )


__all__ = ["benjamini_hochberg", "BHResult"]
