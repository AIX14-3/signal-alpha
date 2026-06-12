"""ScoreCard — 사용자에게 보여줄 최종 형태 (설계 문서 §3 JSON과 1:1).

법적 정책: 투자 권유 표현 금지 — warning + disclaimer 필수 (프로젝트 공통).
점수는 "상승 확률"이 아니라 "KOSPI200 내 상대 우위 백분위"이며, 보정표가
그 점수대의 역사적 실제 분포를 그대로 보여준다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from signal_alpha_harness.calibration import lookup
from signal_alpha_harness.combine import ACTIVE_FACTORS, DRIVER_LABELS

DEFAULT_WARNING = "본 점수는 KOSPI200 내 상대 순위이며 시장 전체가 하락하면 상위 종목도 하락할 수 있습니다."
DEFAULT_DISCLAIMER = "본 자료는 투자 권유가 아니며, 투자 판단의 책임은 투자자 본인에게 있습니다."
DRIVER_Z_THRESHOLD = 0.3


@dataclass(frozen=True)
class ScoreCard:
    ticker: str
    score: int | None  # 0~100 백분위, C등급이면 None (점수 보류)
    confidence: str  # "A" | "B" | "C"
    calibration: dict | None
    drivers: list[str] = field(default_factory=list)
    warning: str = DEFAULT_WARNING
    disclaimer: str = DEFAULT_DISCLAIMER

    def to_dict(self) -> dict:
        return asdict(self)


def _drivers(row: pd.Series) -> list[str]:
    labels = []
    for name in ACTIVE_FACTORS:
        z = row.get(f"z_{name}")
        label = DRIVER_LABELS.get(name, name)
        if pd.isna(z):
            labels.append(f"{label} 결측")
        elif z >= DRIVER_Z_THRESHOLD:
            labels.append(f"{label} +")
        elif z <= -DRIVER_Z_THRESHOLD:
            labels.append(f"{label} -")
        else:
            labels.append(f"{label} 중립")
    return labels


def build_scorecard(row: pd.Series, calibration_table: pd.DataFrame) -> ScoreCard:
    """add_combined_score + add_confidence를 거친 한 행 → ScoreCard."""
    confidence = str(row["confidence"])
    score = row.get("score")
    withheld = confidence == "C" or pd.isna(score)
    return ScoreCard(
        ticker=str(row["ticker"]),
        score=None if withheld else int(round(float(score))),
        confidence=confidence,
        calibration=None if withheld else lookup(calibration_table, float(score)),
        drivers=_drivers(row),
    )


def build_scorecards(
    scored: pd.DataFrame, calibration_table: pd.DataFrame, trade_date
) -> list[ScoreCard]:
    day = scored[scored["trade_date"] == pd.Timestamp(trade_date)]
    return [build_scorecard(row, calibration_table) for _, row in day.iterrows()]
