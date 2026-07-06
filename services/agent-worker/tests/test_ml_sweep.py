"""Tests for the DataLab feature×label sweep engine.

Cover the four mechanisms that make the sweep honest and resumable:
  1. grid enumeration + stable cell keys,
  2. an end-to-end synthetic sweep producing ok cards + a ledger,
  3. resume (a second run does zero new work),
  4. sweep-wide BH-FDR aggregation over the whole ledger,
  5. GATE emission when a dataset spec needs absent credentials.

All offline: synthetic + demo sources need no DB/keys.
"""

from __future__ import annotations

import os

from app.ml import sweep, sweep_grid
from app.ml.sweep_grid import Cell, build_grid


def test_build_grid_enumerates_unique_keys():
    cells = build_grid(source="demo", size="small",
                       extra={"n_stocks": 6, "weeks": 90, "signal_step": 5})
    # small demo grid: 2 labels × 3 families × 2 transforms × 2 models = 24
    assert len(cells) == 24
    keys = [c.key() for c in cells]
    assert len(set(keys)) == len(keys), "cell keys must be unique"


def test_cell_key_stable_and_sensitive():
    a = Cell("demo", "demo", "dir_h5_b0.3", "direction", 5, 0.3, "all", "raw",
             "logistic", 42)
    b = Cell("demo", "demo", "dir_h5_b0.3", "direction", 5, 0.3, "all", "raw",
             "logistic", 42)
    c = Cell("demo", "demo", "dir_h5_b0.3", "direction", 5, 0.3, "all", "raw",
             "ridge", 42)
    assert a.key() == b.key()          # same config → same key
    assert a.key() != c.key()          # model changed → different key
    assert a.panel_key() == c.panel_key()  # but same underlying panel


def test_select_family_and_transform():
    names = ["datalab__momentum_pct", "datalab__spike_ratio", "datalab__observations"]
    assert sweep_grid.select_family(names, "all") == [0, 1, 2]
    assert sweep_grid.select_family(names, "momentum") == [0]
    assert sweep_grid.select_family(names, "spike") == [1]
    # a family with no matching column → empty (engine will skip that cell)
    assert sweep_grid.select_family(["info_0", "noise_1"], "momentum") == []


def test_synthetic_sweep_smoke_and_resume(tmp_path):
    cells = build_grid(source="synthetic", size="small",
                       extra={"n_stocks": 40, "n_dates": 50, "noise": 1.0})
    out = str(tmp_path / "sweep_out")
    summary = sweep.run_sweep(cells, out_dir=out, n_folds=3, n_perm=40, holdout=False)
    assert summary["ran_this_call"] == len(cells)
    assert summary["ok"] >= 1
    assert os.path.exists(os.path.join(out, sweep.LEDGER_NAME))
    assert os.path.exists(os.path.join(out, sweep.REPORT_NAME))

    # Resume: a second identical run must do zero new work.
    summary2 = sweep.run_sweep(cells, out_dir=out, n_folds=3, n_perm=40, holdout=False)
    assert summary2["ran_this_call"] == 0
    assert summary2["total_cells"] == summary["total_cells"]


def test_sweep_wide_fdr_spans_full_ledger():
    # 3 ok cells with a mix of p-values; BH-FDR at q=0.10 over N=3.
    rows = [
        {"key": "a", "status": "ok", "era": "full", "perm_p": 0.001, "rank_ic": 0.2},
        {"key": "b", "status": "ok", "era": "full", "perm_p": 0.60, "rank_ic": 0.0},
        {"key": "c", "status": "ok", "era": "full", "perm_p": 0.90, "rank_ic": -0.1},
        {"key": "g", "status": "gate", "era": "full", "reason": "no creds"},
    ]
    ok, threshold = sweep.fdr_over_ledger(rows, q=0.10)
    assert len(ok) == 3                       # gate row excluded from N
    survive = {r["key"]: r["fdr_survive"] for r in ok}
    assert survive["a"] is True               # 0.001 <= 0.10*1/3
    assert survive["b"] is False
    assert survive["c"] is False
    assert abs(threshold - 0.001) < 1e-12


def test_db_source_gates_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATALAB_SWEEP_DATABASE_URL", raising=False)
    monkeypatch.setattr(sweep_grid, "_load_dotenv_once", lambda: None)  # ignore any .env
    cells = build_grid(
        source="db", size="small",
        extra={"tickers": ("005930", "000660"), "start": "2021-01-01",
               "end": "2023-12-31", "benchmark": "KS11", "prices_csv": None,
               "signal_step": 5},
    )
    out = str(tmp_path / "sweep_db")
    summary = sweep.run_sweep(cells, out_dir=out, n_folds=3, n_perm=20, holdout=False)
    assert summary["gate"] == len(cells)      # every db cell gated, none crashed
    assert summary["ok"] == 0


# --------------------------------------------------------------------------- #
# Magnitude + revenue-nowcast tasks (the search feature×label hunt beyond dir). #
# --------------------------------------------------------------------------- #


def test_magnitude_and_revenue_grids_enumerate_unique_keys():
    # small search grids: 2 labels × 3 families × 2 transforms × 2 models = 24.
    for task in ("magnitude", "revenue"):
        cells = build_grid(source="demo", size="small", task=task,
                           extra={"n_stocks": 8, "weeks": 140, "signal_step": 5})
        assert len(cells) == 24, task
        keys = [c.key() for c in cells]
        assert len(set(keys)) == len(keys), f"{task} keys must be unique"
        assert all(c.task == task for c in cells)


def test_magnitude_demo_sweep_produces_ok_cards(tmp_path):
    cells = build_grid(source="demo", size="small", task="magnitude",
                       extra={"n_stocks": 8, "weeks": 140, "signal_step": 5})
    out = str(tmp_path / "mag")
    summary = sweep.run_sweep(cells, out_dir=out, n_folds=3, n_perm=40, holdout=False)
    assert summary["ok"] >= 1                 # a regressor scored on real magnitude
    assert summary["gate"] == 0


def test_revenue_planted_demo_survives_null_does_not(tmp_path):
    common = {"n_stocks": 14, "weeks": 416, "signal_step": 10}
    # Planted cross-sectional lag1 relationship → at least one cell clears FDR.
    planted = build_grid(source="demo", size="small", task="revenue",
                         extra={**common, "planted": True})
    s_hit = sweep.run_sweep(cells=planted, out_dir=str(tmp_path / "rev_hit"),
                            n_folds=3, n_perm=100, q=0.10, holdout=False)
    assert s_hit["fdr_survivors"] >= 1, "planted search→revenue signal must survive"

    # True-null control (search independent of revenue) → nothing survives.
    null = build_grid(source="demo", size="small", task="revenue",
                      extra={**common, "planted": False})
    s_null = sweep.run_sweep(cells=null, out_dir=str(tmp_path / "rev_null"),
                             n_folds=3, n_perm=100, q=0.10, holdout=False)
    assert s_null["fdr_survivors"] == 0, "true-null must not manufacture a signal"


def test_search_tasks_gate_on_db_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATALAB_SWEEP_DATABASE_URL", raising=False)
    monkeypatch.setattr(sweep_grid, "_load_dotenv_once", lambda: None)  # ignore any .env
    for task in ("magnitude", "revenue"):
        cells = build_grid(
            source="db", size="small", task=task,
            extra={"tickers": ("005930",), "start": "2021-01-01",
                   "end": "2023-12-31", "benchmark": "KS11", "prices_csv": None,
                   "signal_step": 5},
        )
        summary = sweep.run_sweep(cells, out_dir=str(tmp_path / f"db_{task}"),
                                  n_folds=3, n_perm=20, holdout=False)
        assert summary["gate"] == len(cells), task
        assert summary["ok"] == 0, task
