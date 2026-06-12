"""결합 점수의 Phase 3 게이트 CLI — 엔진 본체는 signal_core.quant.combine.

엔진(팩터·결합·보정 로직)은 packages/signal-core로 승격됐다. 하네스는 검증
러너만 갖고, agent-worker 서빙과 같은 코드를 import해 점수 불일치를 차단한다.

Usage (from harness/):

    uv run python -m signal_alpha_harness.combine          # 결합 IC + 보정표 게이트
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# 하위 호환 re-export (기존 테스트·모듈이 harness 경로로 import)
from signal_core.quant.combine import (  # noqa: F401
    ACTIVE_FACTORS,
    DRIVER_LABELS,
    MIN_FACTORS_FOR_SCORE,
    _winsorize_zscore,
    add_combined_score,
)


def main(argv: list[str] | None = None) -> int:
    """결합 점수의 Phase 3 게이트 실행 — 단조성 + 결합 IC ≥ 최고 단독×80%."""
    import argparse
    from dataclasses import asdict
    from datetime import datetime, timezone

    from signal_core.quant.calibration import build_calibration, monotonicity

    from signal_alpha_harness.backtest import DEFAULT_LOG, DEFAULT_PANEL, append_log
    from signal_alpha_harness.factor_eval import DEFAULT_FUNDAMENTALS
    from signal_alpha_harness.metrics import compute_metrics, permutation_pvalue
    from signal_alpha_harness.panel import add_forward_returns, load_panel
    from signal_alpha_harness.splits import chronological_split

    parser = argparse.ArgumentParser(description="결합 점수 게이트")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--permutations", type=int, default=500)
    parser.add_argument("--best-single-ic", type=float, default=0.0339,
                        help="Phase 2 최고 단독 IC (기본: reversal_1m)")
    parser.add_argument("--calibration-out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data" / "calibration.parquet")
    args = parser.parse_args(argv)

    panel = load_panel(args.panel)
    panel = add_forward_returns(panel, (5, 20))
    fundamentals = pd.read_parquet(args.fundamentals) if args.fundamentals.exists() else None

    scored = add_combined_score(panel, fundamentals)
    split = chronological_split(scored["trade_date"])
    tuning = scored[scored["trade_date"].isin(split.train_dates.union(split.valid_dates))]
    train = scored[scored["trade_date"].isin(split.train_dates)]

    # ── 결합 IC (train+valid) ──
    report = compute_metrics(tuning, 20)
    p_value = permutation_pvalue(tuning, 20, n_permutations=args.permutations)
    ic_floor = args.best_single_ic * 0.8
    ic_pass = report.mean_ic is not None and report.mean_ic >= ic_floor and (p_value or 1) < 0.05

    # ── 보정표 (train만 — valid/final은 보정표가 본 적 없어야 함) ──
    table = build_calibration(train, horizon=20)
    mono = monotonicity(table)
    args.calibration_out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(args.calibration_out, index=False)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scorer": "combined:" + "+".join(ACTIVE_FACTORS),
        "segment": "train+valid",
        "note": "phase3 combined gate",
        "panel": str(args.panel),
        "permutations": args.permutations,
        "result": {
            "h20": {**asdict(report), "permutation_p": p_value},
            "coverage": float(tuning["score"].notna().mean()),
            "ic_floor": ic_floor,
            "calibration_monotonicity": mono,
            "calibration": table.to_dict(orient="records"),
        },
    }
    append_log(args.log, record)

    print(f"결합 IC20={report.mean_ic:+.4f} (p={p_value:.4f}, 기준 ≥{ic_floor:+.4f}) "
          f"coverage={record['result']['coverage']:.1%} → {'PASS' if ic_pass else 'FAIL'}")
    print(f"보정표 단조성 spearman={mono['spearman']:+.3f}, top>bottom={mono['top_gt_bottom']} "
          f"→ {'PASS' if mono['passed'] else 'FAIL'}")
    print("\n보정표 (train, h20, 시장 대비 초과수익):")
    print(table.to_string(index=False))
    print(f"\nsaved -> {args.calibration_out}")
    return 0 if (ic_pass and mono["passed"]) else 2


if __name__ == "__main__":
    sys.exit(main())
