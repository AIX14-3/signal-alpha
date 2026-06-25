"""Synthetic panel dataset — fake fuel to smoke-test the harness.

This exists so we can run and SEE the bake-off before any real labels are loaded.
It fabricates a stocks x dates panel where the future excess return depends on a
KNOWN (noisy, partly non-linear) function of a few features, plus pure-noise
columns. A working harness should therefore rank real models above the baseline
and above the noise — that's the smoke test. It says nothing about real markets.

Deterministic given ``seed`` (no global clock/RNG), so runs are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SyntheticDataset:
    X: np.ndarray  # (n, n_features)
    y: np.ndarray  # (n,) direction 1/0 after neutral-band drop
    excess_returns: np.ndarray  # (n,) continuous excess return (%)
    dates: np.ndarray  # (n,) integer day index, sortable
    feature_names: list[str]


def make_synthetic(
    *,
    n_stocks: int = 60,
    n_dates: int = 40,
    n_informative: int = 4,
    n_noise: int = 6,
    noise_scale: float = 1.0,
    neutral_band_pct: float = 0.3,
    seed: int = 7,
) -> SyntheticDataset:
    """Build a reproducible stocks x dates panel with a learnable signal.

    The continuous excess return is ``informative · weights + mild interaction +
    noise``. Rows whose |excess| <= ``neutral_band_pct`` are dropped (mirroring the
    real neutral band). ``noise_scale`` dials how hard the problem is.
    """
    rng = np.random.default_rng(seed)
    n = n_stocks * n_dates

    informative = rng.normal(size=(n, n_informative))
    noise = rng.normal(size=(n, n_noise))
    weights = rng.uniform(0.5, 1.5, size=n_informative) * rng.choice(
        [-1.0, 1.0], size=n_informative
    )

    # Linear signal + one non-linear interaction so tree/kernel models can shine.
    linear = informative @ weights
    interaction = 0.8 * informative[:, 0] * informative[:, 1]
    excess = linear + interaction + rng.normal(scale=noise_scale, size=n)
    # Scale into a plausible percentage range.
    excess = excess / np.std(excess) * 1.2

    dates = np.repeat(np.arange(n_dates), n_stocks)

    X = np.hstack([informative, noise])
    names = [f"info_{i}" for i in range(n_informative)] + [
        f"noise_{i}" for i in range(n_noise)
    ]

    keep = np.abs(excess) > neutral_band_pct
    X, excess, dates = X[keep], excess[keep], dates[keep]
    y = (excess > 0).astype(int)
    return SyntheticDataset(
        X=X,
        y=y,
        excess_returns=excess,
        dates=dates,
        feature_names=names,
    )
