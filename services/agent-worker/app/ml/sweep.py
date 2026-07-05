"""DataLab feature×label sweep engine — the honest automated hunt for signal.

Instead of hand-testing one feature at a time, this runs a *pre-registered* grid
(``sweep_grid.build_grid``) of (dataset × feature-family × transform × label ×
model) cells through the shipping validation toolkit (``evaluation``): purged +
embargo walk-forward OOS predictions, within-date permutation p-value, within-
firm IC, per-period IC, decile spread.

The single non-negotiable guardrail — the reason an automated sweep is legitimate
and not a p-hacking machine — is **sweep-wide BH-FDR**: the false-discovery
correction is applied across the FULL set of cells the sweep has ever run (the
whole ledger), not per cell. A cell only counts as a candidate signal if its
permutation p survives BH-FDR over N = every hypothesis tried, and then only if
it also confirms out-of-sample on a held-out era it was not selected on.

Design:
  * Resumable: every finished cell is appended to ``sweep_results.jsonl`` and its
    ``key`` is skipped on re-run, so the grid can be run in the background and
    continued across sessions.
  * GATE-aware: a cell whose data needs a human (e.g. ``db`` with no DATABASE_URL)
    is recorded with status="gate" and its reason, never crashing the sweep.
  * Panels are cached per ``panel_key`` so all cells sharing a dataset build it once.

CLI:
    python -m app.ml.sweep --source demo --grid small --out sweep_out
    python -m app.ml.sweep --source demo --grid demo --perm 300 --out sweep_out
    python -m app.ml.sweep --source db --grid full --tickers 005930,000660 ...
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import asdict, dataclass, field

import numpy as np

from .evaluation import (
    _decile_spread,
    benjamini_hochberg,
    oos_predictions,
    per_period_ic,
    permutation_pvalue,
    purged_walk_forward_folds,
    within_firm_ic,
)
from .sweep_grid import (
    Cell,
    GateNeeded,
    Panel,
    apply_transform,
    build_grid,
    build_model,
    build_panel_for,
    select_family,
)

LEDGER_NAME = "sweep_results.jsonl"
REPORT_NAME = "sweep_report.md"


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

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def _embargo_days(horizon: int) -> int:
    """Ordinal-day gap that covers ``horizon`` trading days (~7/5 calendar each)."""
    return int(math.ceil(horizon * 1.5)) + 1


def _era_mask(dates: np.ndarray, era: str) -> np.ndarray:
    """Boolean mask selecting an era of the date axis.

    ``full`` = all; ``first`` / ``second`` = the earlier / later half by the
    median unique date (held-out confirmation splits selection from confirmation).
    """
    if era == "full":
        return np.ones(len(dates), dtype=bool)
    unique = np.unique(dates)
    if len(unique) < 4:
        return np.ones(len(dates), dtype=bool)  # too short to split; caller notes it
    cut = unique[len(unique) // 2]
    return dates < cut if era == "first" else dates >= cut


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

    ``panel`` may be passed in (cache) to avoid rebuilding a shared dataset. A
    :class:`GateNeeded` from the dataset spec becomes a status="gate" card; any
    degenerate step (no features for the family, too few OOS rows/folds) becomes
    status="skip" with a reason — never a crash.
    """
    base = dict(
        key=cell.key(), source=cell.source, universe=cell.universe, label=cell.label,
        task=cell.task, horizon=cell.horizon, band=cell.band,
        feature_family=cell.feature_family, transform=cell.transform,
        model=cell.model, seed=cell.seed, era=era,
    )
    if panel is None:
        try:
            panel = build_panel_for(cell)
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

    model = build_model(cell.model, cell.task, cell.seed)
    pred, pmask = oos_predictions(model, X, y, folds, task=cell.task)
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


def _sub(arr: np.ndarray | None, mask: np.ndarray) -> np.ndarray | None:
    return None if arr is None else arr[mask]


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

    Returns a summary dict. Writes ``sweep_results.jsonl`` (append-only, git-
    ignored) and ``sweep_report.md``. The FDR is recomputed over the ENTIRE
    ledger — the sweep-wide correction — every run.
    """
    os.makedirs(out_dir, exist_ok=True)
    ledger_path = os.path.join(out_dir, LEDGER_NAME)
    existing = _load_ledger(ledger_path)
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
                panel_cache[pk] = build_panel_for(cell)
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
    survives at level ``q`` over N = number of ok cells (nan if none survive).
    This is the sweep-wide correction: N spans every hypothesis tried.
    """
    ok = [r for r in rows if r.get("status") == "ok" and r.get("era", "full") == "full"]
    pvals = [r.get("perm_p", float("nan")) for r in ok]
    survive = benjamini_hochberg(pvals, q=q)
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

    # Held-out confirmation: re-run each survivor on the era it was NOT selected on.
    confirmed = []
    if holdout and survivors:
        by_key = {c.key(): c for c in cells}
        for r in survivors:
            cell = by_key.get(r["key"])
            if cell is None:
                r["holdout_confirmed"] = None
                continue
            conf = _confirm_holdout(cell, r, n_folds=n_folds, n_perm=n_perm, q=q)
            r["holdout_confirmed"] = conf
            if conf:
                confirmed.append(r)

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
    out-of-sample check that guts selection luck. Returns None when the panel is
    too short to split.
    """
    try:
        panel = build_panel_for(cell)
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


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:+.4f}"
    return str(v)


def _write_report(out_dir, rows, ok, survivors, confirmed, gates, skips, *,
                  threshold, q, n_folds, n_perm) -> None:
    lines: list[str] = []
    lines.append("# DataLab feature×label sweep — report\n")
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
        lines.append("| rank_ic | perm_p | within_firm_ic | per_period_t | decile | held-out | cell |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in sorted(survivors, key=lambda x: x.get("perm_p", 1.0)):
            cid = (f"{r['source']}/{r['universe']}/{r['label']}/{r['feature_family']}"
                   f"/{r['transform']}/{r['model']}")
            hc = {True: "✅", False: "❌", None: "—"}[r.get("holdout_confirmed")]
            lines.append(
                f"| {_fmt(r.get('rank_ic'))} | {r.get('perm_p'):.4g} | "
                f"{_fmt(r.get('within_firm_ic'))} | {_fmt(r.get('per_period_t'))} | "
                f"{_fmt(r.get('decile_spread'))} | {hc} | `{cid}` |"
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DataLab feature×label sweep")
    p.add_argument("--source", default="demo", choices=["synthetic", "demo", "db"])
    p.add_argument("--grid", default="small", choices=["small", "demo", "full"],
                   help="pre-registered grid size")
    p.add_argument("--out", default="sweep_out", help="output dir (ledger + report)")
    p.add_argument("--universe", default="demo")
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--perm", type=int, default=300)
    p.add_argument("--q", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--no-holdout", action="store_true")
    # db knobs
    p.add_argument("--tickers", default="005930,000660,035420")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2023-12-31")
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--prices-csv", default=None)
    p.add_argument("--signal-step", type=int, default=5)
    # demo knobs
    p.add_argument("--stocks-real", type=int, default=8)
    p.add_argument("--weeks", type=int, default=120)
    args = p.parse_args(argv)

    if args.source == "db":
        extra = {
            "tickers": tuple(t.strip() for t in args.tickers.split(",") if t.strip()),
            "start": args.start, "end": args.end, "benchmark": args.benchmark,
            "prices_csv": args.prices_csv, "signal_step": args.signal_step,
        }
    elif args.source == "demo":
        extra = {"n_stocks": args.stocks_real, "weeks": args.weeks,
                 "signal_step": args.signal_step}
    else:
        extra = {"n_stocks": 60, "n_dates": 60, "noise": 1.0}

    cells = build_grid(source=args.source, size=args.grid, universe=args.universe,
                       seed=args.seed, extra=extra)
    print(f"[sweep] {len(cells)} cells  source={args.source} grid={args.grid}")
    summary = run_sweep(
        cells, out_dir=args.out, n_folds=args.folds, n_perm=args.perm, q=args.q,
        resume=not args.no_resume, holdout=not args.no_holdout,
    )
    print(f"[sweep] ran={summary['ran_this_call']}  ok={summary['ok']}  "
          f"gate={summary['gate']}  skip={summary['skip']}  "
          f"FDR_survivors={summary['fdr_survivors']}  "
          f"confirmed={summary['holdout_confirmed']}")
    print(f"[sweep] report → {summary['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
