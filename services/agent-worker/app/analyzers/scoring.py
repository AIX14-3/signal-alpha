"""Shared scoring transforms for analyzer rule layers.

``graded`` replaces the old step-threshold scoring (indicator over a threshold →
fixed weight) with a smooth, magnitude-aware ``tanh`` mapping:

    score = weight * tanh(value / scale)

Why tanh (vs a dead-zone+ramp): this service does not issue buy/sell calls — it
surfaces an informational signal shown as a percentage. tanh rises gently from
zero and saturates *softly* (asymptotically), so it never pins at the extreme and
needs no hard dead-zone or cap. Noise from tiny samples is handled separately by
the analyzers' small-sample guard, so the transform itself can stay smooth.
"""

from __future__ import annotations

import math


def graded(value: float, *, scale: float, weight: float) -> float:
    """Map a signed magnitude to a signed score in ``(-weight, +weight)``.

    ``score = weight * tanh(value / scale)``. Near zero it is ~linear (small
    input → small score); large inputs approach ±weight without reaching it.
    ``scale`` sets the soft "knee": at ``value == scale`` the score is
    ``0.76 * weight``. Both are in the indicator's own units (e.g. 0.10 = +10%).
    """
    if scale <= 0:
        # Degenerate scale → fall back to a hard sign step at full weight.
        if value > 0:
            return weight
        if value < 0:
            return -weight
        return 0.0
    return weight * math.tanh(value / scale)
