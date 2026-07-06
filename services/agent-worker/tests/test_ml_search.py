"""Tests for the source-agnostic feature×label search engine.

Beyond "does it run", these encode the honesty properties that make an automated
sweep legitimate rather than a p-hacking machine:

  (a) a PLANTED signal survives the whole gauntlet (grid-wide BH-FDR + held-out).
  (b) PURE NOISE produces ZERO held-out-confirmed cells (the false-signal guard).
  (c) sweep-wide BH-FDR is STRICTER than a per-run correction (pooling N hypotheses
      raises the bar, so a borderline p that survives alone can fail in the pool).
  (d) a credential-gated source is recorded as a GATE, never crashes the grid.
"""

from __future__ import annotations

import numpy as np

from app.ml.research.adapters import GateNeeded, build_panel_for, select_family
from app.ml.research.search import (
    fdr_over_ledger,
    run_cell,
    run_sweep,
)
from app.ml.research.search_grid import Cell, build_grid


def _synth_grid(noise: float, seed: int = 42):
    return build_grid(
        source="synthetic", size="small", universe="synth", seed=seed,
        extra={"n_stocks": 60, "n_dates": 60, "noise": noise},
    )


def test_planted_signal_survives_gauntlet(tmp_path):
    """(a) A low-noise synthetic panel has real cross-sectional signal → it must
    clear sweep-wide BH-FDR AND confirm on the held-out era."""
    cells = _synth_grid(noise=1.0)
    summary = run_sweep(cells, out_dir=str(tmp_path), n_folds=4, n_perm=100, q=0.10)
    assert summary["ok"] == len(cells)
    assert summary["fdr_survivors"] >= 1
    assert summary["holdout_confirmed"] >= 1  # the planted signal is real


def test_pure_noise_zero_holdout_confirmed(tmp_path):
    """(b) The false-signal guard: with the signal drowned in noise, NO cell may
    survive the held-out confirmation, even if a lucky perm_p slips through BH."""
    cells = _synth_grid(noise=30.0)
    summary = run_sweep(cells, out_dir=str(tmp_path), n_folds=4, n_perm=100, q=0.10)
    assert summary["ok"] == len(cells)
    assert summary["holdout_confirmed"] == 0


def test_sweep_wide_fdr_is_stricter_than_per_run():
    """(c) Pooling more hypotheses into one BH-FDR can only raise the bar.

    A borderline p that survives BH among a handful of tests must NOT survive once
    a pile of null hypotheses are added to the same correction (sweep-wide N)."""
    def _rows(pvals):
        return [
            {"status": "ok", "era": "full", "perm_p": p, "key": str(i)}
            for i, p in enumerate(pvals)
        ]

    borderline = 0.02
    small = _rows([borderline, 0.9, 0.95])
    ok_small, _ = fdr_over_ledger(small, q=0.10)
    survives_small = sum(r["fdr_survive"] for r in ok_small)

    pooled = _rows([borderline] + [0.9] * 40)
    ok_pooled, _ = fdr_over_ledger(pooled, q=0.10)
    survives_pooled = sum(r["fdr_survive"] for r in ok_pooled)

    assert survives_small >= 1
    assert survives_pooled <= survives_small  # sweep-wide is never more lenient
    assert survives_pooled == 0  # borderline p=0.02 fails BH at N=41, q=0.10


def test_gated_source_records_gate_not_crash(tmp_path):
    """(d) A revenue source with no --revenue-csv (and/or no DATABASE_URL) is a GATE:
    the adapter raises GateNeeded and the sweep records status="gate", never crashes."""
    # The adapter itself gates deterministically (no revenue_csv → GateNeeded,
    # regardless of whether a .env supplies DATABASE_URL).
    try:
        build_panel_for("patent-revenue", horizon=1, band=0.0, seed=42, extra={})
        raised = False
    except GateNeeded:
        raised = True
    assert raised

    cell = Cell(
        source="patent-revenue", universe="u", label="rev_nowcast_q", task="revenue",
        horizon=1, band=0.0, feature_family="all", transform="raw", model="logistic",
        seed=42, extra=(),
    )
    card = run_cell(cell, n_perm=10)
    assert card.status == "gate"
    assert card.reason  # explains what to unblock

    summary = run_sweep([cell], out_dir=str(tmp_path), n_perm=10)
    assert summary["gate"] >= 1
    assert summary["ok"] == 0


def test_resume_ledger_skips_completed(tmp_path):
    """The ledger is resumable: a second run over the same grid adds no new rows."""
    cells = _synth_grid(noise=1.0)
    first = run_sweep(cells, out_dir=str(tmp_path), n_perm=30)
    assert first["ran_this_call"] == len(cells)
    second = run_sweep(cells, out_dir=str(tmp_path), n_perm=30)
    assert second["ran_this_call"] == 0  # all keys already in the ledger


def test_no_features_for_family_skips_honestly(tmp_path):
    """A family whose tokens don't appear in the panel yields no columns → skip,
    not a crash or a train-on-nothing result."""
    panel = build_panel_for("synthetic", horizon=5, band=0.3, seed=42,
                            extra={"n_stocks": 40, "n_dates": 40, "noise": 1.0})
    # synthetic feature names carry none of the datalab tokens.
    assert select_family(panel.feature_names, "dl_momentum") == []

    cell = Cell(
        source="synthetic", universe="u", label="dir_h5_b0.3", task="direction",
        horizon=5, band=0.3, feature_family="dl_momentum", transform="raw",
        model="logistic", seed=42, extra=(("n_stocks", 40), ("n_dates", 40), ("noise", 1.0)),
    )
    card = run_cell(cell, panel=panel, n_perm=10)
    assert card.status == "skip"
    assert card.reason == "no_features_for_family"


def test_fusion_revenue_join_inner_and_rebinarizes():
    """The fusion-revenue join (DB-gated in production) is exercised offline here:
    it must inner-join two revenue sources on (stock, quarter), prefix + concatenate
    their features, keep the shared continuous growth, and RE-binarize on the joined
    cross-section."""
    from collections import Counter

    from app.ml.research.adapters import _join_revenue_panels
    from app.ml.research.datalab_dataset import Dataset

    def _mk(name, rows):  # rows: (sid, date, growth, feat)
        return Dataset(
            X=np.array([[r[3]] for r in rows], dtype=float),
            y=np.zeros(len(rows), dtype=int),
            excess_returns=np.array([r[2] for r in rows], dtype=float),
            dates=np.array([r[1] for r in rows], dtype=int),
            stock_ids=np.array([r[0] for r in rows], dtype=int),
            feature_names=[name], dropped=Counter(),
        )

    hi = _mk("hv", [(s, 100, 0.1 * s, 1.0 * s) for s in range(1, 9)])
    pt = _mk("pv", [(s, 100, 0.1 * s, 2.0 * s) for s in range(1, 9)] + [(9, 100, 5.0, 9.0)])
    panel = _join_revenue_panels(hi, pt, min_cross_section=6)

    assert panel.feature_names == ["h::hv", "p::pv"]
    assert len(panel.y) == 8  # the unshared patent key (9,100) is dropped
    assert panel.task == "revenue"
    # median split of growths 0.1..0.8 → lower half 0, upper half 1
    assert panel.y.tolist() == [0, 0, 0, 0, 1, 1, 1, 1]
    assert np.allclose(panel.excess_returns, [0.1 * s for s in range(1, 9)])
    assert np.allclose(panel.X[:, 1], [2.0 * s for s in range(1, 9)])


def test_scorecard_json_roundtrip(tmp_path):
    """Ledger rows serialize with the new within_firm_verdict field intact."""
    cells = _synth_grid(noise=1.0)[:1]
    run_sweep(cells, out_dir=str(tmp_path), n_perm=20)
    import json
    import os
    with open(os.path.join(str(tmp_path), "search_results.jsonl"), encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    assert "within_firm_verdict" in row
    assert row["source"] == "synthetic"
    assert np.isfinite(row["perm_p"])
