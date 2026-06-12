"""결합 점수 — 게이트 통과 팩터의 z-score 고정 등가중 → KOSPI200 백분위(0~100).

규율 (설계 문서 §1-2): **가중치 탐색 루프 금지.** 가중치는 등가중 상수로
커밋되며, 변경은 "팩터의 게이트 통과/탈락"으로만 일어난다.

Phase 2 게이트 통과 팩터 (2026-06-12, experiments.jsonl):
  reversal_1m (+0.0339, p=.002) · lowvol_60 (+0.0267, p=.002)
  · quality_margin_yoy (+0.0182, p=.030)
momentum_12_1·quality_margin은 탈락(IC≤0), flow·value는 데이터 보류.

Usage (from harness/):

    uv run python -m signal_alpha_harness.combine          # 결합 IC + 보정표 게이트
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from signal_alpha_harness.factors import FACTORS

# 고정 구성 — Phase 2 게이트 산출물. 탐색 금지.
ACTIVE_FACTORS: tuple[str, ...] = ("reversal_1m", "lowvol_60", "quality_margin_yoy")
WINSOR_SIGMA = 3.0
MIN_FACTORS_FOR_SCORE = 2  # 미만이면 점수 보류 (confidence C와 연동)

# 사용자 표시용 드라이버 라벨
DRIVER_LABELS = {
    "reversal_1m": "단기반전",
    "lowvol_60": "저변동성",
    "quality_margin_yoy": "마진개선",
}


def _winsorize_zscore(values: pd.Series) -> pd.Series:
    """한 거래일의 횡단면: ±3σ 윈저라이즈 후 z-score (std=0이면 NaN)."""
    mean, std = values.mean(), values.std()
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.nan, index=values.index)
    clipped = values.clip(mean - WINSOR_SIGMA * std, mean + WINSOR_SIGMA * std)
    mean_c, std_c = clipped.mean(), clipped.std()
    if not np.isfinite(std_c) or std_c == 0:
        return pd.Series(np.nan, index=values.index)
    return (clipped - mean_c) / std_c


def add_combined_score(
    panel: pd.DataFrame,
    fundamentals: pd.DataFrame | None,
    factors: tuple[str, ...] = ACTIVE_FACTORS,
) -> pd.DataFrame:
    """panel에 z_<factor>·n_factors_used·score(0~100 백분위) 컬럼을 붙인다.

    결측 팩터는 그 종목·그 날짜에서 제외(등가중 평균은 가용 팩터로만).
    가용 팩터가 MIN_FACTORS_FOR_SCORE 미만이면 score=NaN (점수 보류).
    """
    result = panel.copy()
    z_columns: list[str] = []
    for name in factors:
        raw = FACTORS[name](panel, fundamentals).astype(float)
        z = raw.groupby(result["trade_date"], sort=False).transform(_winsorize_zscore)
        column = f"z_{name}"
        result[column] = z
        z_columns.append(column)

    z_matrix = result[z_columns]
    result["n_factors_used"] = z_matrix.notna().sum(axis=1)
    combined = z_matrix.mean(axis=1, skipna=True)
    combined = combined.where(result["n_factors_used"] >= MIN_FACTORS_FOR_SCORE)

    # 일별 횡단면 백분위 0~100 (KOSPI200 내 상대 우위)
    result["score"] = (
        combined.groupby(result["trade_date"], sort=False).rank(pct=True) * 100.0
    )
    return result


def main(argv: list[str] | None = None) -> int:
    """결합 점수의 Phase 3 게이트 실행 — 단조성 + 결합 IC ≥ 최고 단독×80%."""
    import argparse
    from dataclasses import asdict
    from datetime import datetime, timezone

    from signal_alpha_harness.backtest import DEFAULT_LOG, DEFAULT_PANEL, append_log
    from signal_alpha_harness.calibration import build_calibration, monotonicity
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
