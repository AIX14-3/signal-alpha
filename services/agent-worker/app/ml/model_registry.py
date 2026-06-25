"""Model registry — the "게이트 통과 모델만" gate of architecture.mermaid.

Two independent gates decide which vendored vol-benchmark models actually run:

  1. **Backtest gate** (``gate_passed``): only models that won a backtest
     (vol-benchmark ``comparison.csv``: DM<0 & p<0.05 vs the EWMA baseline) are
     whitelisted. The whitelist is config-driven via ``ML_GATE_PASSED_MODELS``;
     the default below is the price-only CPU set. The EWMA baseline is always
     kept as a reference model.
  2. **Availability gate** (``is_available``): a whitelisted model is still
     skipped if its backend is not importable on this host — ``arch`` for GARCH,
     ``lightgbm`` for LightGBM, ``torch`` for the CPU TCN, torch + the model repo
     for the GPU models. This is what lets the TCN auto-exclude where torch is not
     installed and the GPU models (Kronos/Chronos-2) auto-exclude on CPU-only hosts.

Heavy model modules are imported lazily (only when a model is both whitelisted
and being resolved), so resolving the default CPU set never imports torch.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Per-host availability is read from these module-level flags (set by each model
# module's top-level ``try: import backend``). A module without any of these flags
# is pure numpy/pandas and therefore always available.
_AVAILABILITY_FLAGS = ("HAVE_ARCH", "HAVE_LGBM", "HAVE_TORCH", "HAVE_LIB")

PredictFn = Callable[..., float]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    module: str
    device: str = "cpu"  # 'cpu' | 'gpu'
    cfg: dict[str, Any] = field(default_factory=dict)

    def _module(self) -> Any:
        return importlib.import_module(self.module)

    def load(self) -> PredictFn:
        """Import the model module and return its ``predict`` callable."""
        return self._module().predict

    def is_available(self) -> bool:
        """True if the backend this model needs is importable on this host."""
        try:
            module = self._module()
        except Exception:  # noqa: BLE001 — a missing backend = unavailable, not fatal
            return False
        for flag in _AVAILABILITY_FLAGS:
            if hasattr(module, flag):
                return bool(getattr(module, flag))
        return True  # pure numpy/pandas model (e.g. EWMA, HAR-RV)


# All vendored models. Membership here does NOT mean a model runs — the backtest
# gate (gate_passed_names) and availability gate decide that.
_CANDIDATES: tuple[ModelSpec, ...] = (
    ModelSpec("ewma", "vol_models.models.cpu_ewma", "cpu"),
    ModelSpec("har_rv", "vol_models.models.cpu_harrv", "cpu"),
    ModelSpec("garch", "vol_models.models.cpu_garch", "cpu"),
    ModelSpec("lightgbm", "vol_models.models.cpu_lgbm", "cpu"),
    ModelSpec("tcn", "vol_models.models.cpu_tcn", "cpu", {"window": 48, "epochs": 150, "hidden": 16}),
    ModelSpec("kronos", "vol_models.models.gpu_kronos", "gpu", {"n_samples": 100, "context_len": 400}),
    ModelSpec("chronos2", "vol_models.models.gpu_chronos2", "gpu", {"context_len": 512}),
)
_BY_NAME = {spec.name: spec for spec in _CANDIDATES}

# Backtest-accepted whitelist. Override with ML_GATE_PASSED_MODELS="ewma,har_rv,..".
# Default = ML 3종(ewma/har_rv/garch) + lightweight TCN DL line (torch-gated;
# auto-skips where torch is not installed). LightGBM stays a vendored candidate
# but is OFF by default (heavy: pulls scikit-learn) — re-enable via the env
# override. GPU models stay off until validated on a GPU host.
DEFAULT_GATE_PASSED = ("ewma", "har_rv", "garch", "tcn")


def all_specs() -> tuple[ModelSpec, ...]:
    return _CANDIDATES


def gate_passed_names() -> list[str]:
    """Backtest-accepted model names (env override or the default CPU set)."""
    raw = os.getenv("ML_GATE_PASSED_MODELS")
    names = (
        [n.strip() for n in raw.split(",") if n.strip()]
        if raw not in (None, "")
        else list(DEFAULT_GATE_PASSED)
    )
    # ignore unknown names so a typo can't crash the pipeline
    return [n for n in names if n in _BY_NAME]


def resolve_models(enabled: list[str] | None = None) -> list[ModelSpec]:
    """Specs that pass BOTH gates: backtest-accepted AND backend-available.

    ``enabled`` optionally narrows to an explicit subset (still subject to both
    gates) — useful for tests and targeted runs.
    """
    gate = gate_passed_names()
    if enabled is not None:
        wanted = set(enabled)
        gate = [n for n in gate if n in wanted]
    return [_BY_NAME[n] for n in gate if _BY_NAME[n].is_available()]
