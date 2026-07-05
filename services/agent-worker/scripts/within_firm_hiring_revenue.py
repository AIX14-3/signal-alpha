#!/usr/bin/env python
"""[채용 Step1·최우선] within-firm 게이트 CLI — 채용→매출 나우캐스트: timing이냐 정적특성이냐.

confirmed 신호(decision_tree rankIC +0.128, clean 94종목·분기·BH-FDR 생존)에 within-firm
분해가 아직 미적용이라, 이 rankIC 가 **트레이더블 timing**(기업이 자기 평소보다 더 뽑을 때
자기 평소보다 매출↑)인지 **정적 종목특성**(원래 많이 뽑고 원래 잘 크는 기업들의 횡단면 상관)
인지 가른다. within_ic≈0 & 비유의면 특허 선례처럼 강등.

라벨 = 매출 YoY 성장률(Dataset.excess_returns 슬롯; 주가 방향 아님 — 방향/매그니튜드는 기각·재시도 금지).

실행(작업 디렉터리 services/agent-worker, .env 의 DATABASE_URL·매출 CSV 필요):
    python scripts/within_firm_hiring_revenue.py \\
        --tickers <clean-KOSPI200> --revenue-csv revenue_dart.csv \\
        --signal-freq quarterly --feature-set volume+duty --precise-rematch \\
        --min-obs 2 --min-cross-section 6 --n-perm 200

핵심 로직은 app.ml.research.within_firm_gate.gate_report (단위테스트 대상). 이 파일은 로더+CLI뿐.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# scripts/ 실행 시 agent-worker 루트를 import 경로에 추가(app 패키지 노출).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.ml.research.fundamentals_dataset import load_from_env  # noqa: E402
from app.ml.research.within_firm_gate import gate_report, render_gate  # noqa: E402


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tickers", required=True,
                   help="comma-separated clean-KOSPI200 tickers")
    p.add_argument("--revenue-csv", required=True,
                   help="fundamentals_dart.build_revenue_csv 산출 CSV")
    p.add_argument("--signal-freq", choices=["quarterly"], default="quarterly",
                   help="build_revenue_dataset 은 분기말 as_of 고정. monthly 는 후속(Step2).")
    p.add_argument("--feature-set", default="volume+duty",
                   choices=["volume", "duty", "volume+duty"],
                   help="rich(직군 세분 AI/HW/SW)=duty 포함. 기본 volume+duty.")
    p.add_argument("--lookback", type=int, default=90)
    p.add_argument("--min-obs", type=int, default=2, help="종목당 최소 공고 관측")
    p.add_argument("--min-cross-section", type=int, default=6)
    p.add_argument("--precise-rematch", action="store_true", default=True)
    p.add_argument("--model", default="decision_tree", help="주 신호 모델(confirmed=decision_tree)")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-perm", type=int, default=200)
    p.add_argument("--min-obs-per-firm", type=int, default=2,
                   help="within 분해에 기여할 기업의 최소 관측(1개는 demean=0이라 제외)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # 리포트에 한글·기호(≈—🟢)가 있어 Windows 기본 콘솔(cp949)에서 print 가 깨진다 → utf-8 강제.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    args = _parse_args(argv)
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL required (services/agent-worker/.env)")

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    ds = asyncio.run(
        load_from_env(
            database_url=database_url,
            revenue_csv=args.revenue_csv,
            tickers=tickers,
            lookback_days=args.lookback,
            feature_set=args.feature_set,
            min_observations=args.min_obs,
            min_cross_section=args.min_cross_section,
            precise_rematch=args.precise_rematch,
        )
    )
    print(f"[revenue] samples={len(ds)}  features={len(ds.feature_names)}  "
          f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
          f"quarters={len(np.unique(ds.dates)) if len(ds) else 0}  "
          f"dropped={dict(ds.dropped)}")
    if len(ds) == 0:
        raise SystemExit("No samples — revenue_csv/HIRING for these tickers?")

    report = gate_report(
        ds,
        model_name=args.model,
        n_folds=args.folds,
        seed=args.seed,
        n_perm=args.n_perm,
        min_obs_per_firm=args.min_obs_per_firm,
    )
    print(render_gate(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
