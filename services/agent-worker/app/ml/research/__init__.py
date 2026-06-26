"""Model bake-off harness for alternative-data scoring (offline research).

This package trains and compares many ML models on the SAME data with the SAME
time-aware evaluation, so we can see which (if any) actually beats a trivial
baseline at judging future price moves.

Design notes
------------
- ``labels``/``features`` are pure (no third-party deps) so they import even when
  scikit-learn is absent. Heavy bits (``models``, ``evaluation``) import lazily.
- The harness runs on SYNTHETIC data out of the box (see ``synthetic``); real
  labels come later from the scoring batch that fills ``backtest_results``.
- Nothing here is imported by the running service. ML deps live in the optional
  ``ml`` dependency group, so the container is unaffected unless explicitly built
  with extras. This package is intentionally separate from the operational
  inference layer in ``app.ml`` (inference / model_registry / meta_learner).
"""
