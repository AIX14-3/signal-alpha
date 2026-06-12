"""확신도 등급 A/B/C — "모르면 모른다고 출력한다" (설계 원칙 5).

C 등급이면 점수 자체를 보류한다. 두 축:
  ① 데이터 완전성 — 그 종목·그 날짜에 실제로 쓰인 팩터 수 (n_factors_used)
  ② 시장 국면 — 유니버스 등가중 일수익률의 20일 실현변동성이 과거 분포에서
     차지하는 백분위. **expanding 백분위**라 T일 등급은 T일까지의 역사만 본다
     (point-in-time — 미래 변동성 분포를 미리 아는 것을 차단).
"""

from __future__ import annotations

import pandas as pd

VOL_WINDOW = 20
VOL_WARMUP = 252  # 백분위가 의미를 갖기 위한 최소 역사
GRADE_B_VOL_PCTILE = 0.80
GRADE_C_VOL_PCTILE = 0.95


def market_vol_percentile(panel: pd.DataFrame) -> pd.Series:
    """일별 시장(등가중) 수익률의 20일 변동성 → expanding 백분위 (trade_date 인덱스)."""
    close = panel["close"].astype(float).where(panel["close"] > 0)
    returns = close.groupby(panel["ticker"], sort=False).pct_change()
    market = returns.groupby(panel["trade_date"], sort=False).mean()
    market = market.sort_index()
    vol = market.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std()
    pct = vol.expanding(min_periods=VOL_WARMUP).rank(pct=True)
    return pct


def grade_row(n_factors_used: int, vol_pctile: float | None, total_factors: int) -> str:
    if n_factors_used < 2 or (vol_pctile is not None and vol_pctile > GRADE_C_VOL_PCTILE):
        return "C"
    if n_factors_used < total_factors or vol_pctile is None or vol_pctile > GRADE_B_VOL_PCTILE:
        return "B"
    return "A"


def add_confidence(scored: pd.DataFrame, total_factors: int) -> pd.DataFrame:
    """add_combined_score 결과에 confidence 컬럼 부여 (C는 score 보류와 일치)."""
    result = scored.copy()
    vol_pct = market_vol_percentile(scored)
    aligned = result["trade_date"].map(vol_pct)
    result["confidence"] = [
        grade_row(int(n), None if pd.isna(v) else float(v), total_factors)
        for n, v in zip(result["n_factors_used"], aligned)
    ]
    return result
