"""QUANT 일일 배치 — KOSPI 유니버스 횡단면 점수를 DB에 적재.

점수 엔진은 packages/signal-core(signal_core.quant)와 **동일 코드** — 하네스
검증과 서빙의 점수가 어긋나는 순간 검증 체계가 무너지므로, 여기서는 데이터
로드와 적재만 한다.

횡단면 점수는 종목 하나로는 계산할 수 없으므로 SourcePipeline(종목 단위
collect→analyze)이 아니라 배치다. 적재 경로: analysis_results(run_key=
QUANT_DAILY, 멱등 upsert) → quant_scores(1:1).

현 단계 가용 팩터: 가격계 2종 (reversal_1m, lowvol_60).
- quality_margin_yoy: DB fundamentals 테이블에 공시일(available_date) 컬럼이
  없어 point-in-time 결합 불가 — 스키마 보강(후속 마이그레이션) 전까지 결측.
  따라서 n_factors_used=2 → confidence는 B가 상한이다 (의도된 동작).
- flow/value: 수급 백필·시총 수집 후 (하네스와 동일한 보류 사유).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd
from signal_core.quant.calibration import load_default_table, lookup
from signal_core.quant.combine import ACTIVE_FACTORS, add_combined_score
from signal_core.quant.confidence import add_confidence
from signal_core.quant.scorecard import ScoreCard, drivers_from_row

from signal_alpha_data_access.repositories.analysis import AnalysisRepository
from signal_alpha_data_access.repositories.scoring import ScoringRepository

logger = logging.getLogger("quant_runner")

RUN_KEY = "QUANT_DAILY"
PANEL_LOOKBACK_CALENDAR_DAYS = 200  # lowvol 60영업일 + 휴장 여유
AVAILABLE_SOURCES = ["PRICE"]
MISSING_SOURCES = ["FUNDAMENTAL", "FLOW", "VALUE"]

# quant_scores.source_agreement CHECK는 HIGH/MEDIUM/LOW — 확신도 등급을 매핑해
# 적재하고, 원래 등급(A/B/C)은 score_breakdown JSON에 보존한다.
AGREEMENT_BY_CONFIDENCE = {"A": "HIGH", "B": "MEDIUM", "C": "LOW"}

_PANEL_SQL = """
SELECT s.id AS stock_id, s.ticker, s.name,
       o.trade_date, o.close, o.volume, o.foreign_net, o.institution_net
FROM ohlcv_data o
JOIN stocks s ON s.id = o.stock_id
WHERE s.is_active = TRUE AND o.trade_date >= $1
ORDER BY s.ticker, o.trade_date
"""


async def load_price_panel(pool: Any, lookback_days: int = PANEL_LOOKBACK_CALENDAR_DAYS) -> pd.DataFrame:
    since = date.today() - timedelta(days=lookback_days)
    rows = await pool.fetch(_PANEL_SQL, since)
    if not rows:
        return pd.DataFrame()
    panel = pd.DataFrame([dict(row) for row in rows])
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["close"] = panel["close"].astype(float)
    return panel


def score_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """최신 거래일의 점수·확신도·드라이버 행들 (엔진은 signal-core)."""
    scored = add_combined_score(panel, fundamentals=None)
    graded = add_confidence(scored, total_factors=len(ACTIVE_FACTORS))
    latest = graded["trade_date"].max()
    return graded[graded["trade_date"] == latest]


async def run_daily(pool: Any) -> dict:
    """일일 배치 본체 — 같은 날짜 재실행은 upsert로 멱등."""
    panel = await load_price_panel(pool)
    if panel.empty:
        logger.warning("ohlcv_data가 비어 있음 — 백필 전이면 정상")
        return {"analysis_date": None, "scored": 0, "withheld": 0, "universe": 0}

    day_rows = score_panel(panel)
    analysis_date = day_rows["trade_date"].max().date()

    analysis = AnalysisRepository(pool)
    scoring = ScoringRepository(pool)
    scored_count = 0
    withheld = 0
    for _, row in day_rows.iterrows():
        if pd.isna(row["score"]) or row["confidence"] == "C":
            withheld += 1
            continue
        score = float(row["score"])
        card = ScoreCard(
            ticker=str(row["ticker"]),
            score=int(round(score)),
            confidence=str(row["confidence"]),
            calibration=lookup(load_default_table(), score),
            drivers=drivers_from_row(row),
        )
        result = await analysis.upsert_analysis_result(
            stock_id=int(row["stock_id"]),
            analysis_date=analysis_date,
            run_key=RUN_KEY,
            source_signal_event_ids=[],
            base_score=card.score,
            warning=card.warning,
            disclaimer=card.disclaimer,
        )
        await scoring.upsert_quant_score(
            result_id=int(result["id"]),
            stock_id=int(row["stock_id"]),
            score_breakdown=json.dumps(
                {
                    "score": card.score,
                    "confidence": card.confidence,
                    "drivers": card.drivers,
                    "calibration": card.calibration,
                    "n_factors_used": int(row["n_factors_used"]),
                    "z": {
                        name: None if pd.isna(row[f"z_{name}"]) else round(float(row[f"z_{name}"]), 4)
                        for name in ACTIVE_FACTORS
                    },
                },
                ensure_ascii=False,
            ),
            overall_score=card.score,
            available_sources=AVAILABLE_SOURCES,
            missing_sources=MISSING_SOURCES,
            source_agreement=AGREEMENT_BY_CONFIDENCE[card.confidence],
        )
        scored_count += 1

    logger.info(
        "quant daily done: date=%s scored=%d withheld=%d universe=%d",
        analysis_date, scored_count, withheld, day_rows["ticker"].nunique(),
    )
    return {
        "analysis_date": str(analysis_date),
        "scored": scored_count,
        "withheld": withheld,
        "universe": int(day_rows["ticker"].nunique()),
    }


def scorecard_from_db_row(row: Any) -> ScoreCard:
    """quant_scores 조회 행 → ScoreCard (적재 시 저장한 breakdown 재사용)."""
    breakdown = row["score_breakdown"]
    if isinstance(breakdown, str):
        breakdown = json.loads(breakdown)
    return ScoreCard(
        ticker=str(row["ticker"]),
        score=breakdown.get("score"),
        confidence=str(breakdown.get("confidence", row["source_agreement"])),
        calibration=breakdown.get("calibration"),
        drivers=list(breakdown.get("drivers", [])),
        warning=row["warning"] or ScoreCard.__dataclass_fields__["warning"].default,
        disclaimer=row["disclaimer"] or ScoreCard.__dataclass_fields__["disclaimer"].default,
    )
