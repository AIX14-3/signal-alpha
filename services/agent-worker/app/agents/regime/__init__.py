"""Market-regime tagging agent — opt-in, non-verdict evidence layer (Track A).

Why: we proved a hiring→revenue "developer-hiring timing" edge was actually a
SECTOR wave (it vanished under sector-neutralization). Lesson: market/sector-flow
CONTROL must be deterministic (numbers), while an LLM's legitimate role is regime
TAGGING / context / rationale on top. This package encodes that split.

Iron principle (mirrors ``app/agents/dart/evidence.py`` and the DataLab cause
tag): **numbers are owned by the deterministic layer; the LLM only emits an
evidence tag, never a verdict / score / direction.** The classifier returns a
``RegimeTag`` (enum label + one-line rationale + confidence + model provenance)
and NOTHING that a scoring path could read as a number.

Two integration channels (see docs/proposals/2026-07-07-regime-tagging-layer.md):
  (A) evidence card → ``method_detail`` (display/audit only), and
  (B) a SEPARATE deterministic PIT ``regime__*`` feature (``features.regime_features``)
      joined in ``app/ml/source_features.assemble_features`` for the meta-learner.
The LLM output from (A) is NEVER read back into (B) — the feature is recomputed
point-in-time from deterministic inputs (mirrors the DataLab ML-boundary rule).

Default OFF: ``build_regime_tagger`` returns ``None`` when ``regime_use_llm`` is
off OR no Gemini key is configured — a byte-identical no-op off-path. This module
is a STUB helper: nothing here is wired into a production handler/scoring path.
"""

from __future__ import annotations

from app.agents.regime.classifier import (
    REGIME_LABELS,
    RegimeClassifier,
    RegimeTag,
    build_regime_tagger,
    classify_regime,
)
from app.agents.regime.features import regime_features

__all__ = [
    "REGIME_LABELS",
    "RegimeClassifier",
    "RegimeTag",
    "build_regime_tagger",
    "classify_regime",
    "regime_features",
]
