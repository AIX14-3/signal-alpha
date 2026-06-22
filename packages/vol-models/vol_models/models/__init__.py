"""Benchmark model modules (vendored from vol-benchmark).

Each module exposes ``NAME`` and ``predict(contract, asof_idx, horizon, cfg, rng)``.
Models are NOT imported here so that selecting a light model never pulls a heavy
backend (arch/lightgbm/torch); import the specific module you need.
"""
