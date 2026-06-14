"""matplotlib charts for the backtest report (saved as PNG)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from config import CHARTS_DIR, HORIZONS  # noqa: E402


def _save(fig, name: str) -> str:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    path = CHARTS_DIR / name
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return name


def chart_accuracy_by_horizon(per_horizon: dict) -> str:
    horizons = list(HORIZONS)
    rule = [per_horizon[h]["rule"]["all"].get("accuracy", 0) * 100 for h in horizons]
    ml = [per_horizon[h]["ml"]["all"].get("accuracy", 0) * 100 for h in horizons]
    base = [per_horizon[h]["rule"]["all"].get("majority_acc", 0) * 100 for h in horizons]

    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(horizons))
    ax.bar([i - 0.25 for i in x], rule, 0.25, label="Rule")
    ax.bar([i for i in x], ml, 0.25, label="ML")
    ax.bar([i + 0.25 for i in x], base, 0.25, label="Majority baseline", color="#bbb")
    ax.axhline(50, color="red", ls="--", lw=0.8, label="50% (coin flip)")
    ax.set_xticks(list(x)); ax.set_xticklabels(horizons)
    ax.set_ylabel("Hit rate (%)"); ax.set_title("OOS directional hit-rate by horizon")
    ax.legend(fontsize=8)
    return _save(fig, "1_accuracy_by_horizon.png")


def chart_regime(per_horizon: dict) -> str:
    horizons = list(HORIZONS)
    full = [per_horizon[h]["rule"]["all"].get("accuracy", 0) * 100 for h in horizons]
    ai = [per_horizon[h]["rule"]["ai_era"].get("accuracy", 0) * 100 for h in horizons]
    pre = [per_horizon[h]["rule"]["pre_ai"].get("accuracy", 0) * 100 for h in horizons]

    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(horizons))
    ax.bar([i - 0.25 for i in x], full, 0.25, label="Full 10y")
    ax.bar([i for i in x], pre, 0.25, label="Pre-AI")
    ax.bar([i + 0.25 for i in x], ai, 0.25, label="AI era (2022-11+)")
    ax.axhline(50, color="red", ls="--", lw=0.8)
    ax.set_xticks(list(x)); ax.set_xticklabels(horizons)
    ax.set_ylabel("Hit rate (%)"); ax.set_title("Rule model hit-rate by regime")
    ax.legend(fontsize=8)
    return _save(fig, "2_regime.png")


def chart_equity(per_horizon: dict) -> str:
    eq = per_horizon["1w"]["rule"]["equity"]["curve"]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(eq)
    ax.set_title("Illustrative rule equity (1w, with costs) — NOT a trading sim")
    ax.set_ylabel("Growth of 1"); ax.set_xlabel("OOS trades")
    return _save(fig, "3_equity.png")


def chart_per_symbol(per_symbol: dict) -> str:
    syms = list(per_symbol)
    acc = [per_symbol[s]["1w"]["rule"].get("accuracy", 0) * 100 for s in syms]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(syms, acc, color="#4878a8")
    ax.axhline(50, color="red", ls="--", lw=0.8)
    ax.set_ylabel("Hit rate (%)"); ax.set_title("Per-symbol rule hit-rate (1w, OOS)")
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=8)
    return _save(fig, "4_per_symbol.png")


def make_all(results: dict) -> list[str]:
    return [
        chart_accuracy_by_horizon(results["per_horizon"]),
        chart_regime(results["per_horizon"]),
        chart_equity(results["per_horizon"]),
        chart_per_symbol(results["per_symbol"]),
    ]
