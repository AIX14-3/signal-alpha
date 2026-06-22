"""ML/DL inference layer.

Bridges signal-alpha's collected price history to the vendored vol-benchmark
models (``vol_models``). PR1 ships the point-in-time DataContract adapter; PR2+
add the model registry / inference / meta-learner stages.
"""
