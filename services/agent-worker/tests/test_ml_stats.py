"""Unit tests for the Benjamini-Hochberg FDR correction (app.ml.research.stats)."""
from __future__ import annotations

import math

from app.ml.research.stats import benjamini_hochberg


# Canonical Benjamini & Hochberg (1995) worked example: 15 p-values, alpha=0.05
# rejects exactly the 4 smallest.
_BH95 = [
    0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344,
    0.0459, 0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.000,
]


def test_bh95_rejects_first_four():
    res = benjamini_hochberg(_BH95, alpha=0.05)
    assert res.n_rejected == 4
    assert sum(res.rejected) == 4
    # the four smallest p-values are exactly the rejected ones
    assert res.rejected[:4] == [True, True, True, True]
    assert not any(res.rejected[4:])


def test_qvalues_monotone_nondecreasing_in_pvalue_order():
    res = benjamini_hochberg(_BH95, alpha=0.05)
    # input is already ascending in p -> q must be non-decreasing
    qs = res.qvalues
    for a, b in zip(qs, qs[1:]):
        assert a <= b + 1e-12
    assert all(0.0 <= q <= 1.0 for q in qs)


def test_preserves_original_order():
    # shuffle the canonical example; rejection must follow the values, not position
    idx = [14, 0, 7, 3, 1, 9, 2, 8, 5, 11, 4, 13, 6, 10, 12]
    shuffled = [_BH95[i] for i in idx]
    res = benjamini_hochberg(shuffled, alpha=0.05)
    assert res.n_rejected == 4
    # positions whose original p-value was among the 4 smallest
    smallest4 = set(sorted(range(len(_BH95)), key=lambda i: _BH95[i])[:4])
    for pos, orig_i in enumerate(idx):
        assert res.rejected[pos] == (orig_i in smallest4)


def test_nan_and_none_treated_as_one():
    res = benjamini_hochberg([0.001, float("nan"), None, 0.9], alpha=0.05)
    # only the single tiny p can be rejected (m=4 -> threshold 0.0125 for rank1)
    assert res.rejected[0] is True
    assert res.rejected[1] is False and res.rejected[2] is False
    assert math.isclose(res.qvalues[1], 1.0) and math.isclose(res.qvalues[2], 1.0)


def test_empty_and_all_null():
    assert benjamini_hochberg([]).n_rejected == 0
    res = benjamini_hochberg([0.5, 0.6, 0.7], alpha=0.05)
    assert res.n_rejected == 0
    assert not any(res.rejected)


def test_bonferroni_is_more_conservative_than_bh():
    # with correlated-looking near-threshold p-values, BH rejects >= Bonferroni
    pvals = [0.001, 0.01, 0.02, 0.03, 0.04]
    m = len(pvals)
    bonf = sum(1 for p in pvals if p <= 0.05 / m)  # threshold 0.01
    bh = benjamini_hochberg(pvals, alpha=0.05).n_rejected
    assert bh >= bonf
