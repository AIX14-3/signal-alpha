"""Backtest runner: panel → score → split → metrics ×3 → permutation gate → log.

Usage (from the repository root):

    uv run python -m signal_alpha_harness.backtest --segment train
    uv run python -m signal_alpha_harness.backtest --segment valid --note "w_flow 0.15→0.2"
    uv run python -m signal_alpha_harness.backtest --walk-forward
    uv run python -m signal_alpha_harness.backtest --segment final --unlock-final  # 단 1회

Every run appends one JSON line to harness/experiments.jsonl — the loop's
append-only history. Runs are refused if the panel file is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from signal_alpha_harness.baseline_score import add_baseline_score
from signal_alpha_harness.metrics import compute_metrics, permutation_pvalue
from signal_alpha_harness.panel import add_forward_returns, load_panel
from signal_alpha_harness.splits import chronological_split, segment_dates, walk_forward_windows

DEFAULT_PANEL = Path(__file__).resolve().parents[1] / "data" / "panel_kospi200.parquet"
DEFAULT_LOG = Path(__file__).resolve().parents[1] / "experiments.jsonl"
HORIZONS = (5, 20)


def run_segment(
    scored: pd.DataFrame,
    dates: pd.DatetimeIndex,
    *,
    n_permutations: int,
    thresholds: tuple[float, float] = (0.2, -0.2),
) -> dict:
    frame = scored[scored["trade_date"].isin(dates)]
    result: dict = {"n_days": int(frame["trade_date"].nunique())}
    for horizon in HORIZONS:
        report = compute_metrics(
            frame, horizon, positive_threshold=thresholds[0], negative_threshold=thresholds[1]
        )
        result[f"h{horizon}"] = asdict(report)
        if n_permutations > 0:
            result[f"h{horizon}"]["permutation_p"] = permutation_pvalue(
                frame, horizon, n_permutations=n_permutations
            )
    return result


def append_log(log_path: Path, record: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def format_report(record: dict) -> str:
    lines = [f"segment={record['segment']} scorer={record['scorer']} note={record['note'] or '-'}"]
    for horizon in HORIZONS:
        block = record["result"].get(f"h{horizon}")
        if not block:
            continue
        hit = block["hit_rate"]
        ic = block["mean_ic"]
        spread = block["quantile_spread"]
        p = block.get("permutation_p")
        lines.append(
            f"  h{horizon:>2}: hit={hit:.3f}({block['n_directional']}건) " if hit is not None
            else f"  h{horizon:>2}: hit=n/a "
        )
        lines[-1] += (
            f"IC={ic:+.4f} (양봉비율 {block['ic_positive_share']:.2f}) " if ic is not None
            else "IC=n/a "
        )
        lines[-1] += f"spread={spread:+.4%} " if spread is not None else "spread=n/a "
        lines[-1] += f"perm_p={p:.4f}" if p is not None else "perm_p=skip"
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the harness backtest")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--scorer", choices=["baseline", "quant"], default="baseline",
                        help="quant = Phase 3 결합 점수 (0~100 백분위)")
    parser.add_argument("--fundamentals", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data" / "fundamentals_kospi200.parquet")
    parser.add_argument("--segment", choices=["train", "valid", "final"], default="train")
    parser.add_argument("--walk-forward", action="store_true", help="train+valid 워크포워드 국면표")
    parser.add_argument("--regimes", action="store_true", help="train+valid 국면별 IC 분해")
    parser.add_argument("--unlock-final", action="store_true")
    parser.add_argument("--permutations", type=int, default=500)
    parser.add_argument("--note", default="", help="이번 회차에서 바꾼 파라미터 1개 설명")
    args = parser.parse_args(argv)

    if not args.panel.exists():
        print(f"panel not found: {args.panel} — collect_panel을 먼저 실행하세요", file=sys.stderr)
        return 1

    panel = load_panel(args.panel)
    panel = add_forward_returns(panel, HORIZONS)
    if args.scorer == "quant":
        from signal_alpha_harness.combine import add_combined_score

        fundamentals = pd.read_parquet(args.fundamentals) if args.fundamentals.exists() else None
        scored = add_combined_score(panel, fundamentals)
        thresholds = (80.0, 20.0)  # 백분위 점수의 방향 콜 기준
        scorer_name = "quant_combined"
    else:
        scored = add_baseline_score(panel)
        thresholds = (0.2, -0.2)
        scorer_name = "baseline_price_lite"
    split = chronological_split(scored["trade_date"])

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scorer": scorer_name,
        "segment": args.segment,
        "note": args.note,
        "panel": str(args.panel),
        "permutations": args.permutations,
    }

    if args.regimes:
        from signal_alpha_harness.regime import regime_ic_breakdown

        tuning_dates = split.train_dates.union(split.valid_dates)
        tuning = scored[scored["trade_date"].isin(tuning_dates)]
        breakdown = regime_ic_breakdown(tuning, horizon=20)
        record["segment"] = "regimes(train+valid)"
        record["result"] = {"regimes_h20": breakdown.to_dict(orient="records")}
        append_log(args.log, record)
        print(breakdown.to_string(index=False))
        bear = breakdown[breakdown["regime"] == "bear"]["mean_ic"].iloc[0]
        print(f"\n하락장 IC 게이트: {bear if bear is not None else 'n/a'} → "
              f"{'PASS' if bear is not None and bear > 0 else 'FAIL'}")
        return 0

    if args.walk_forward:
        tuning_dates = split.train_dates.union(split.valid_dates)
        windows = walk_forward_windows(tuning_dates)
        record["segment"] = "walk_forward"
        record["result"] = {
            "windows": [
                {
                    "test_start": str(test.min().date()),
                    "test_end": str(test.max().date()),
                    **run_segment(scored, test, n_permutations=0, thresholds=thresholds),
                }
                for _, test in windows
            ]
        }
        append_log(args.log, record)
        for window in record["result"]["windows"]:
            h5, h20 = window.get("h5", {}), window.get("h20", {})
            print(
                f"{window['test_start']}~{window['test_end']}: "
                f"IC5={h5.get('mean_ic')} IC20={h20.get('mean_ic')} "
                f"hit5={h5.get('hit_rate')} hit20={h20.get('hit_rate')}"
            )
        return 0

    dates = segment_dates(split, args.segment, unlock_final=args.unlock_final)
    record["result"] = run_segment(
        scored, dates, n_permutations=args.permutations, thresholds=thresholds
    )
    append_log(args.log, record)
    print(format_report(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
