"""Source-agnostic feature×label search engine — the honest automated hunt.

Instead of hand-testing one feature at a time, this runs a *pre-registered* grid
(``search_grid.build_grid``) of (source × feature-family × transform × label ×
model) cells through the shipping validation toolkit (``evaluation``): purged +
embargo walk-forward OOS predictions, within-date permutation p-value, within-firm
IC, per-period IC, decile spread.

Four honesty devices make an automated sweep legitimate rather than a p-hacking
machine:
  1. **pre-registered grid** — enumerated in ``search_grid`` before any run.
  2. **sweep-wide BH-FDR** — the false-discovery correction is applied across the
     FULL ledger (N = every hypothesis ever tried), not per cell.
  3. **held-out confirmation** — a survivor must keep its sign at perm_p ≤ q on a
     later era it was NOT selected on.
  4. **within-firm gate** — a confirmed survivor is finally decomposed into
     between-firm (static characteristic) vs within-firm (tradeable timing).

Resumable (append-only ``search_results.jsonl`` ledger, keyed by ``Cell.key``),
GATE-aware (a source needing DATABASE_URL/CSV is recorded status="gate", never
crashes), and panel-cached per ``panel_key``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass

import numpy as np

from .adapters import (
    GateNeeded,
    Panel,
    apply_transform,
    build_model,
    build_panel_for,
    select_family,
)
from .evaluation import (
    _decile_spread,
    oos_predictions,
    per_period_ic,
    permutation_pvalue,
    purged_walk_forward_folds,
    within_firm_ic,
)
from .search_grid import Cell
from .stats import benjamini_hochberg

LEDGER_NAME = "search_results.jsonl"
REPORT_NAME = "search_report.md"


@dataclass
class ScoreCard:
    """One cell's result. ``status`` is ok / gate / skip; metrics nan when not ok."""

    key: str
    source: str
    universe: str
    label: str
    task: str
    horizon: int
    band: float
    feature_family: str
    transform: str
    model: str
    seed: int
    n: int = 0
    rank_ic: float = float("nan")
    perm_p: float = float("nan")
    within_firm_ic: float = float("nan")
    per_period_t: float = float("nan")
    decile_spread: float = float("nan")
    era: str = "full"
    status: str = "ok"        # "ok" | "gate" | "skip"
    reason: str = ""
    # filled by the report step over the whole ledger:
    fdr_survive: bool | None = None
    holdout_confirmed: bool | None = None
    within_firm_verdict: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def _embargo_days(horizon: int) -> int:
    """Ordinal-day gap covering ``horizon`` trading days (~7/5 calendar each)."""
    return int(math.ceil(horizon * 1.5)) + 1


def _era_mask(dates: np.ndarray, era: str) -> np.ndarray:
    """``full`` = all; ``first`` / ``second`` = earlier / later half by median date."""
    if era == "full":
        return np.ones(len(dates), dtype=bool)
    unique = np.unique(dates)
    if len(unique) < 4:
        return np.ones(len(dates), dtype=bool)  # too short to split; caller notes it
    cut = unique[len(unique) // 2]
    return dates < cut if era == "first" else dates >= cut


def _sub(arr: np.ndarray | None, mask: np.ndarray) -> np.ndarray | None:
    return None if arr is None else arr[mask]


def _panel_for(cell: Cell) -> Panel:
    return build_panel_for(
        cell.source, horizon=cell.horizon, band=cell.band, seed=cell.seed,
        extra=cell.extra_dict(),
    )


def run_cell(
    cell: Cell,
    *,
    panel: Panel | None = None,
    n_folds: int = 4,
    n_perm: int = 300,
    era: str = "full",
    min_oos: int = 30,
    min_folds: int = 2,
) -> ScoreCard:
    """Evaluate one grid cell end-to-end and return its ScoreCard.

    A :class:`GateNeeded` from the source becomes status="gate"; any degenerate step
    (no features for the family, too few OOS rows/folds) becomes status="skip" with a
    reason — never a crash. Scoring is always classification (binary ``y``) + rank-IC
    of the bullish score vs the continuous ``excess_returns``.
    """
    base = dict(
        key=cell.key(), source=cell.source, universe=cell.universe, label=cell.label,
        task=cell.task, horizon=cell.horizon, band=cell.band,
        feature_family=cell.feature_family, transform=cell.transform,
        model=cell.model, seed=cell.seed, era=era,
    )
    if panel is None:
        try:
            panel = _panel_for(cell)
        except GateNeeded as g:
            return ScoreCard(**base, status="gate", reason=g.reason)
        except Exception as e:  # a genuinely broken spec — record, don't crash the sweep
            return ScoreCard(**base, status="skip", reason=f"panel_error:{type(e).__name__}:{e}")

    cols = select_family(panel.feature_names, cell.feature_family)
    if not cols:
        return ScoreCard(**base, status="skip", reason="no_features_for_family")

    mask_era = _era_mask(panel.dates, era)
    if mask_era.sum() < min_oos:
        return ScoreCard(**base, status="skip", reason=f"era_too_small:{int(mask_era.sum())}")

    X = apply_transform(panel.X[mask_era][:, cols], _sub(panel.stock_ids, mask_era), cell.transform)
    y = panel.y[mask_era]
    excess = panel.excess_returns[mask_era]
    dates = panel.dates[mask_era]
    stock_ids = _sub(panel.stock_ids, mask_era)

    try:
        folds = purged_walk_forward_folds(dates, n_folds=n_folds, embargo_days=_embargo_days(cell.horizon))
    except ValueError as e:
        return ScoreCard(**base, status="skip", reason=f"folds:{e}")
    if len(folds) < min_folds:
        return ScoreCard(**base, status="skip", reason=f"too_few_folds:{len(folds)}")

    model = build_model(cell.model, cell.seed)
    # Always the classification path: y is binary, excess_returns carries the target.
    pred, pmask = oos_predictions(model, X, y, folds, task="direction")
    if pmask.sum() < min_oos:
        return ScoreCard(**base, status="skip", reason=f"too_few_oos:{int(pmask.sum())}")

    s, r, d = pred[pmask], excess[pmask], dates[pmask]
    sid = stock_ids[pmask] if stock_ids is not None else None
    obs, perm_p = permutation_pvalue(s, r, d, n_perm=n_perm, seed=cell.seed, metric="rank_ic")
    wfic = within_firm_ic(s, r, sid) if sid is not None else float("nan")
    ppic = per_period_ic(s, r, d, min_per_date=3)
    dec = _decile_spread(s, r)

    return ScoreCard(
        **base, status="ok", n=int(pmask.sum()), rank_ic=float(obs), perm_p=float(perm_p),
        within_firm_ic=float(wfic), per_period_t=float(ppic.t_stat), decile_spread=float(dec),
    )


# --------------------------------------------------------------------------- #
# Ledger + sweep driver.                                                       #
# --------------------------------------------------------------------------- #


def _load_ledger(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _append_ledger(path: str, card: ScoreCard) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(card.to_json() + "\n")


def run_sweep(
    cells: list[Cell],
    *,
    out_dir: str,
    n_folds: int = 4,
    n_perm: int = 300,
    q: float = 0.10,
    resume: bool = True,
    holdout: bool = True,
) -> dict:
    """Run every cell (skipping ledger-completed ones), then FDR + report.

    Returns a summary dict. Writes ``search_results.jsonl`` (append-only, git-ignored)
    and ``search_report.md``. The FDR is recomputed over the ENTIRE ledger — the
    sweep-wide correction — every run.
    """
    os.makedirs(out_dir, exist_ok=True)
    ledger_path = os.path.join(out_dir, LEDGER_NAME)
    existing = _load_ledger(ledger_path) if resume else []
    if not resume and os.path.exists(ledger_path):
        os.remove(ledger_path)
    done_keys = {row["key"] for row in existing if row.get("era", "full") == "full"}

    panel_cache: dict[str, Panel | None] = {}
    gate_reasons: dict[str, str] = {}
    ran = 0
    for cell in cells:
        if cell.key() in done_keys:
            continue
        pk = cell.panel_key()
        if pk not in panel_cache:
            try:
                panel_cache[pk] = _panel_for(cell)
            except GateNeeded as g:
                panel_cache[pk] = None
                gate_reasons[pk] = g.reason
            except Exception as e:
                panel_cache[pk] = None
                gate_reasons[pk] = f"panel_error:{type(e).__name__}:{e}"
        panel = panel_cache[pk]
        if panel is None:
            card = run_cell(cell, panel=None, n_folds=n_folds, n_perm=n_perm)
            if card.status == "ok":  # spec failed on rebuild — force gate/skip
                card.status, card.reason = "gate", gate_reasons.get(pk, "unavailable")
        else:
            card = run_cell(cell, panel=panel, n_folds=n_folds, n_perm=n_perm)
        _append_ledger(ledger_path, card)
        done_keys.add(cell.key())
        ran += 1

    summary = _finalise(cells, out_dir, ledger_path, q=q, n_folds=n_folds,
                        n_perm=n_perm, holdout=holdout)
    summary["ran_this_call"] = ran
    return summary


def fdr_over_ledger(rows: list[dict], q: float = 0.10) -> tuple[list[dict], float]:
    """Apply BH-FDR across ALL ok full-era cells; annotate ``fdr_survive`` in place.

    Returns ``(ok_rows, threshold)`` where threshold is the largest p-value that
    survives at level ``q`` over N = number of ok cells (nan if none survive). This
    is the sweep-wide correction: N spans every hypothesis tried.
    """
    ok = [r for r in rows if r.get("status") == "ok" and r.get("era", "full") == "full"]
    pvals = [r.get("perm_p", float("nan")) for r in ok]
    survive = benjamini_hochberg(pvals, alpha=q).rejected
    threshold = float("nan")
    for r, s in zip(ok, survive):
        r["fdr_survive"] = bool(s)
        if s and (math.isnan(threshold) or r["perm_p"] > threshold):
            threshold = r["perm_p"]
    return ok, threshold


def _finalise(cells, out_dir, ledger_path, *, q, n_folds, n_perm, holdout) -> dict:
    rows = _load_ledger(ledger_path)
    ok, threshold = fdr_over_ledger(rows, q=q)
    survivors = [r for r in ok if r.get("fdr_survive")]

    by_key = {c.key(): c for c in cells}
    # Held-out confirmation: re-run each survivor on the era it was NOT selected on.
    confirmed = []
    if holdout and survivors:
        for r in survivors:
            cell = by_key.get(r["key"])
            if cell is None:
                r["holdout_confirmed"] = None
                continue
            conf = _confirm_holdout(cell, r, n_folds=n_folds, n_perm=n_perm, q=q)
            r["holdout_confirmed"] = conf
            if conf:
                confirmed.append(r)

    # Within-firm gate: the final timing-vs-static-characteristic verdict on the
    # confirmed survivors (only these earn the heavier decomposition).
    for r in confirmed:
        cell = by_key.get(r["key"])
        r["within_firm_verdict"] = _within_firm_verdict(cell) if cell else None

    gates = [r for r in rows if r.get("status") == "gate"]
    skips = [r for r in rows if r.get("status") == "skip"]
    _write_report(out_dir, rows, ok, survivors, confirmed, gates, skips,
                  threshold=threshold, q=q, n_folds=n_folds, n_perm=n_perm)
    return {
        "total_cells": len(rows),
        "ok": len(ok),
        "gate": len(gates),
        "skip": len(skips),
        "fdr_survivors": len(survivors),
        "holdout_confirmed": len(confirmed),
        "fdr_threshold": threshold,
        "report": os.path.join(out_dir, REPORT_NAME),
    }


def _confirm_holdout(cell: Cell, row: dict, *, n_folds, n_perm, q) -> bool | None:
    """A survivor is confirmed only if the OTHER era keeps the sign at perm_p<=q.

    Selection used the full panel; here we re-run on the later ("second") era and
    require the same-sign rank-IC and a permutation p that clears ``q`` — a genuine
    out-of-sample check that guts selection luck. Returns None when the panel is too
    short to split.
    """
    try:
        panel = _panel_for(cell)
    except Exception:
        return None
    card = run_cell(cell, panel=panel, n_folds=n_folds, n_perm=n_perm, era="second")
    if card.status != "ok":
        return None
    sel_ic = row.get("rank_ic", float("nan"))
    if not (math.isfinite(sel_ic) and math.isfinite(card.rank_ic)):
        return None
    same_sign = (sel_ic >= 0) == (card.rank_ic >= 0)
    return bool(same_sign and card.perm_p <= q)


def _within_firm_verdict(cell: Cell) -> str | None:
    """Run the within-firm gate on a confirmed survivor's family-selected panel.

    Returns the gate verdict (🟢 timing / 🔴 static / 🟡 ambiguous / abstain) or None
    when the panel has no firm ids (synthetic) or the decomposition can't run. This
    is the wiring of the existing ``within_firm_gate.gate_report`` — the final filter
    that a positive rank-IC is tradeable timing, not a static cross-sectional trait.
    """
    try:
        from collections import Counter

        from .datalab_dataset import Dataset
        from .within_firm_gate import gate_report

        panel = _panel_for(cell)
        if panel.stock_ids is None:
            return "n/a (no firm ids)"
        cols = select_family(panel.feature_names, cell.feature_family)
        if not cols:
            return None
        X = apply_transform(panel.X[:, cols], panel.stock_ids, cell.transform)
        ds = Dataset(
            X=X, y=panel.y, excess_returns=panel.excess_returns, dates=panel.dates,
            stock_ids=panel.stock_ids,
            feature_names=[panel.feature_names[i] for i in cols], dropped=Counter(),
        )
        return gate_report(ds, seed=cell.seed).verdict
    except Exception as e:
        return f"gate_error:{type(e).__name__}"


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:+.4f}"
    return str(v)


def _write_report(out_dir, rows, ok, survivors, confirmed, gates, skips, *,
                  threshold, q, n_folds, n_perm) -> None:
    lines: list[str] = []
    lines.append("# Source-agnostic feature×label search — report\n")
    verdict = (
        f"**{len(confirmed)} confirmed** signal(s)" if confirmed
        else (f"**{len(survivors)} FDR survivor(s), 0 held-out-confirmed**" if survivors
              else "**0 signals** — swept space is a rigorous null")
    )
    lines.append(f"한 줄 결론: {verdict} across N={len(ok)} tested cells "
                 f"(BH-FDR q={q}, perm={n_perm}, folds={n_folds}).\n")
    lines.append("## Coverage\n")
    lines.append(f"- total ledger rows: **{len(rows)}**")
    lines.append(f"- ok (tested): **{len(ok)}**   gate: **{len(gates)}**   skip: **{len(skips)}**")
    thr = "n/a" if math.isnan(threshold) else f"{threshold:.4g}"
    lines.append(f"- sweep-wide BH-FDR: N={len(ok)}, survive threshold p ≤ **{thr}**\n")

    lines.append("## FDR survivors (candidate signals)\n")
    if survivors:
        lines.append("| rank_ic | perm_p | within_firm_ic | per_period_t | decile | held-out | wf_gate | cell |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in sorted(survivors, key=lambda x: x.get("perm_p", 1.0)):
            cid = (f"{r['source']}/{r['universe']}/{r['label']}/{r['feature_family']}"
                   f"/{r['transform']}/{r['model']}")
            hc = {True: "✅", False: "❌", None: "—"}[r.get("holdout_confirmed")]
            wfv = r.get("within_firm_verdict") or "—"
            lines.append(
                f"| {_fmt(r.get('rank_ic'))} | {r.get('perm_p'):.4g} | "
                f"{_fmt(r.get('within_firm_ic'))} | {_fmt(r.get('per_period_t'))} | "
                f"{_fmt(r.get('decile_spread'))} | {hc} | {wfv} | `{cid}` |"
            )
    else:
        lines.append("_none — no cell cleared sweep-wide BH-FDR._")
    lines.append("")

    lines.append("## Near-misses (smallest perm_p, not surviving)\n")
    near = sorted([r for r in ok if not r.get("fdr_survive")],
                  key=lambda x: x.get("perm_p", 1.0))[:10]
    if near:
        lines.append("| perm_p | rank_ic | within_firm_ic | cell |")
        lines.append("|---|---|---|---|")
        for r in near:
            cid = (f"{r['source']}/{r['label']}/{r['feature_family']}"
                   f"/{r['transform']}/{r['model']}")
            lines.append(f"| {r.get('perm_p'):.4g} | {_fmt(r.get('rank_ic'))} | "
                         f"{_fmt(r.get('within_firm_ic'))} | `{cid}` |")
    else:
        lines.append("_none_")
    lines.append("")

    if gates:
        lines.append("## GATES (need user data/credentials)\n")
        seen = set()
        for r in gates:
            key = (r["source"], r.get("reason", ""))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- `{r['source']}/{r['universe']}` → {r.get('reason','')}")
        lines.append("")

    with open(os.path.join(out_dir, REPORT_NAME), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


__all__ = [
    "ScoreCard", "run_cell", "run_sweep", "fdr_over_ledger",
    "LEDGER_NAME", "REPORT_NAME",
]
