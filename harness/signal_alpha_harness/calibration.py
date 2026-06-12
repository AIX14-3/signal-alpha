"""보정표 — 신뢰도의 본체.

"점수 78 = 오를 확률 78%"가 아니라 "과거 이 점수대 종목들의 실제 20일
시장 대비 초과수익 분포가 이랬다"를 그대로 내놓는다 (설계 문서 §0·§3).

시장 기준은 유니버스 등가중 평균 수익률 — 별도 지수 데이터 의존 없이
패널만으로 계산되고, '시장 대비 상대 우위'라는 점수 정의와 일치한다.

보정표는 **train 구간만으로** 만든다 — valid/final/섀도가 보정표를 본 적
없어야 OOS 증거가 된다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BUCKET_WIDTH = 10  # 점수 10점 구간 → 0~9 버킷


def add_excess_return(frame: pd.DataFrame, horizon: int) -> pd.Series:
    """fwd_ret_h − 같은 날 유니버스 등가중 평균 fwd_ret_h."""
    column = f"fwd_ret_{horizon}"
    market = frame.groupby("trade_date", sort=False)[column].transform("mean")
    return frame[column] - market


def build_calibration(frame: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    """점수 10점 구간별 초과수익 분포표 (median, p25, p75, n)."""
    scored = frame.dropna(subset=["score", f"fwd_ret_{horizon}"]).copy()
    scored["excess"] = add_excess_return(scored, horizon)
    # score=100은 마지막 버킷에 포함
    scored["bucket"] = np.clip(scored["score"] // BUCKET_WIDTH, 0, 9).astype(int)

    rows = []
    for bucket, group in scored.groupby("bucket"):
        rows.append(
            {
                "bucket": int(bucket),
                "score_range": f"{bucket * BUCKET_WIDTH}~{bucket * BUCKET_WIDTH + BUCKET_WIDTH}",
                "horizon_days": horizon,
                "n": int(len(group)),
                "median_excess": float(group["excess"].median()),
                "p25_excess": float(group["excess"].quantile(0.25)),
                "p75_excess": float(group["excess"].quantile(0.75)),
            }
        )
    return pd.DataFrame(rows).sort_values("bucket").reset_index(drop=True)


def monotonicity(table: pd.DataFrame) -> dict:
    """게이트: 버킷 순서와 중앙값의 Spearman ≥ 0.7 그리고 최상위 > 최하위."""
    medians = table.sort_values("bucket")["median_excess"].to_numpy()
    order = np.arange(len(medians))
    rank_a = pd.Series(medians).rank().to_numpy()
    spearman = float(np.corrcoef(order, rank_a)[0, 1]) if len(medians) > 1 else float("nan")
    top_gt_bottom = bool(medians[-1] > medians[0])
    return {
        "spearman": spearman,
        "top_gt_bottom": top_gt_bottom,
        "passed": bool(spearman >= 0.7 and top_gt_bottom),
    }


def lookup(table: pd.DataFrame, score: float) -> dict | None:
    """ScoreCard용: 점수가 속한 버킷의 분포 행을 dict로 반환."""
    bucket = int(np.clip(score // BUCKET_WIDTH, 0, 9))
    rows = table[table["bucket"] == bucket]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return {
        "horizon_days": int(row["horizon_days"]),
        "median_excess_return": f"{row['median_excess']:+.1%}",
        "p25_p75": [f"{row['p25_excess']:+.1%}", f"{row['p75_excess']:+.1%}"],
        "sample_size": int(row["n"]),
    }
