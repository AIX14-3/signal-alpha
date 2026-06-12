"""Derived baseline builders (not raw collectors).

These tools READ external sources (e.g. Naver DataLab) and compute statistical
baselines, writing only to baseline tables such as ``hiring_baseline``. They do
NOT write raw_documents / *_raw_details / processing_queue — that is the raw
collectors' contract.
"""
from app.baseline.hiring_baseline_builder import (
    BaselineStats,
    DataLabBaselineCollector,
    compute_baseline,
)

__all__ = [
    "BaselineStats",
    "DataLabBaselineCollector",
    "compute_baseline",
]
