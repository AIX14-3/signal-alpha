"""결합 점수 엔진 — 게이트 통과 팩터의 z-score 고정 등가중 → 백분위(0~100).

규율 (신뢰도 우선 설계 §1-2): **가중치 탐색 루프 금지.** 가중치는 등가중
상수로 커밋되며, 변경은 "팩터의 게이트 통과/탈락"으로만 일어난다.

이 모듈은 하네스(검증)와 agent-worker(서빙)가 **같은 코드**를 import한다 —
두 환경의 점수가 달라지는 순간 검증 체계가 무너지므로 여기서만 수정한다.

Phase 2 게이트 통과 팩터 (2026-06-12, harness/experiments.jsonl):
  reversal_1m (+0.0339, p=.002) · lowvol_60 (+0.0267, p=.002)
  · quality_margin_yoy (+0.0182, p=.030)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_core.quant.factors import FACTORS

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

    # 일별 횡단면 백분위 0~100 (유니버스 내 상대 우위)
    result["score"] = (
        combined.groupby(result["trade_date"], sort=False).rank(pct=True) * 100.0
    )
    return result
