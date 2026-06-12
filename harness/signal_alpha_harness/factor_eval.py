"""팩터별 단독 IC 검증 러너 (Phase 2 게이트).

Usage (from harness/):

    uv run python -m signal_alpha_harness.factor_eval --all
    uv run python -m signal_alpha_harness.factor_eval --factor momentum_12_1

평가 구간은 train+valid (final 20%는 splits의 잠금 그대로 — 여기서 풀지 않는다).
각 실행은 experiments.jsonl에 1줄씩 기록된다 (scorer="factor:<이름>").

게이트 (설계 문서 Phase 2): 평가 가능한 팩터 중 **3개 이상이 h20 기준
mean IC > 0 이고 순열검정 p < 0.05** — 실패 시 모델이 아니라 Phase 1
데이터 버그부터 의심한다.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from signal_alpha_harness.backtest import DEFAULT_LOG, DEFAULT_PANEL, append_log
from signal_core.quant.factors import DATA_PENDING, FACTORS
from signal_alpha_harness.metrics import compute_metrics, permutation_pvalue
from signal_alpha_harness.panel import add_forward_returns, load_panel
from signal_alpha_harness.splits import chronological_split

DEFAULT_FUNDAMENTALS = Path(__file__).resolve().parents[1] / "data" / "fundamentals_kospi200.parquet"
HORIZONS = (5, 20)
GATE_HORIZON = 20
GATE_P = 0.05
GATE_MIN_FACTORS = 3


def evaluate_factor(
    name: str,
    panel: pd.DataFrame,
    fundamentals: pd.DataFrame | None,
    *,
    n_permutations: int,
) -> dict:
    factor_fn = FACTORS[name]
    scored = panel.copy()
    scored["score"] = factor_fn(panel, fundamentals).astype(float)

    split = chronological_split(scored["trade_date"])
    tuning_dates = split.train_dates.union(split.valid_dates)
    frame = scored[scored["trade_date"].isin(tuning_dates)]

    result: dict = {
        "n_days": int(frame["trade_date"].nunique()),
        "coverage": float(frame["score"].notna().mean()),
    }
    for horizon in HORIZONS:
        report = compute_metrics(frame, horizon)
        block = asdict(report)
        if n_permutations > 0 and report.mean_ic is not None:
            block["permutation_p"] = permutation_pvalue(frame, horizon, n_permutations=n_permutations)
        result[f"h{horizon}"] = block
    return result


def gate_status(result: dict) -> bool | None:
    block = result.get(f"h{GATE_HORIZON}") or {}
    ic = block.get("mean_ic")
    p = block.get("permutation_p")
    if ic is None or p is None:
        return None
    return ic > 0 and p < GATE_P


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="팩터 단독 IC 검증")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--factor", choices=sorted(FACTORS), default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--permutations", type=int, default=500)
    parser.add_argument("--include-pending", action="store_true",
                        help="데이터 보류 팩터(flow/value)도 강제 평가")
    args = parser.parse_args(argv)

    if not args.panel.exists():
        print(f"panel not found: {args.panel}", file=sys.stderr)
        return 1
    panel = load_panel(args.panel)
    panel = add_forward_returns(panel, HORIZONS)
    fundamentals = pd.read_parquet(args.fundamentals) if args.fundamentals.exists() else None

    if args.factor:
        names = [args.factor]
    elif args.all:
        names = [n for n in FACTORS if args.include_pending or n not in DATA_PENDING]
    else:
        parser.error("--factor 또는 --all 필요")

    passed = 0
    for name in names:
        result = evaluate_factor(name, panel, fundamentals, n_permutations=args.permutations)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scorer": f"factor:{name}",
            "segment": "train+valid",
            "note": "phase2 single-factor IC",
            "panel": str(args.panel),
            "permutations": args.permutations,
            "result": result,
        }
        append_log(args.log, record)

        verdict = gate_status(result)
        passed += 1 if verdict else 0
        h20 = result.get("h20", {})
        ic = h20.get("mean_ic")
        p = h20.get("permutation_p")
        spread = h20.get("quantile_spread")
        print(
            f"{name:<20} coverage={result['coverage']:.1%} "
            + (f"IC20={ic:+.4f} " if ic is not None else "IC20=n/a ")
            + (f"p={p:.4f} " if p is not None else "p=n/a ")
            + (f"spread20={spread:+.4%} " if spread is not None else "spread20=n/a ")
            + ("PASS" if verdict else "FAIL" if verdict is not None else "NO-DATA")
        )

    print(
        f"\n게이트: {passed}/{len(names)} 팩터 통과 (기준: h{GATE_HORIZON} IC>0 & p<{GATE_P}, "
        f"필요 {GATE_MIN_FACTORS}개) → {'GATE PASS' if passed >= GATE_MIN_FACTORS else 'GATE FAIL'}"
    )
    return 0 if passed >= GATE_MIN_FACTORS else 2


if __name__ == "__main__":
    sys.exit(main())
