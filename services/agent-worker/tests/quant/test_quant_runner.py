"""QUANT 일일 배치 — 묵업 ohlcv 패널로 적재 경로 전체를 검증."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import date, timedelta

import pytest

import app.quant.runner as runner

TICKERS = [f"{100000 + k}"[-6:] for k in range(8)]  # 100000~100007 형태 6자리


def make_mock_rows(n_days: int = 150) -> list[dict]:
    """묵업 ohlcv 행 — 종목별로 추세·변동성을 다르게 줘 점수가 퍼지게 한다."""
    rows = []
    start = date.today() - timedelta(days=int(n_days * 1.5))
    day = start
    produced = 0
    while produced < n_days:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        produced += 1
        for k, ticker in enumerate(TICKERS):
            drift = (k - 4) * 0.001  # -0.4% ~ +0.3% 일간 추세
            wiggle = 1 + 0.002 * (k + 1) * ((-1) ** produced)  # 종목별 변동성 차등
            close = 1000.0 * math.exp(drift * produced) * wiggle
            rows.append(
                {
                    "stock_id": k + 1,
                    "ticker": ticker,
                    "name": f"묵업종목{k}",
                    "trade_date": day,
                    "close": round(close, 2),
                    "volume": 10_000 + k,
                    "foreign_net": None,
                    "institution_net": None,
                }
            )
    return rows


class FakePool:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.queries.append((sql, args))
        return self.rows


class FakeAnalysisRepository:
    def __init__(self, pool) -> None:
        self.upserts: list[dict] = []

    async def upsert_analysis_result(self, **kwargs):
        self.upserts.append(kwargs)
        return {"id": len(self.upserts)}


class FakeScoringRepository:
    def __init__(self, pool) -> None:
        self.upserts: list[dict] = []

    async def upsert_quant_score(self, **kwargs):
        self.upserts.append(kwargs)
        return kwargs


@pytest.fixture()
def fakes(monkeypatch):
    analysis = FakeAnalysisRepository(None)
    scoring = FakeScoringRepository(None)
    monkeypatch.setattr(runner, "AnalysisRepository", lambda pool: analysis)
    monkeypatch.setattr(runner, "ScoringRepository", lambda pool: scoring)
    return analysis, scoring


def test_run_daily_scores_mock_universe(fakes):
    analysis, scoring = fakes
    pool = FakePool(make_mock_rows())

    stats = asyncio.run(runner.run_daily(pool))

    assert stats["universe"] == len(TICKERS)
    assert stats["scored"] + stats["withheld"] == len(TICKERS)
    assert stats["scored"] == len(analysis.upserts) == len(scoring.upserts)
    assert stats["scored"] > 0

    # analysis_results: 멱등 run_key + 법적 고지 필수
    first = analysis.upserts[0]
    assert first["run_key"] == "QUANT_DAILY"
    assert 0 <= first["base_score"] <= 100
    assert first["warning"] and first["disclaimer"]

    # quant_scores: breakdown에 드라이버·보정분포·팩터 z가 들어간다
    breakdown = json.loads(scoring.upserts[0]["score_breakdown"])
    assert set(breakdown["z"]) == set(runner.ACTIVE_FACTORS)
    assert breakdown["calibration"]["horizon_days"] == 20
    assert len(breakdown["drivers"]) == len(runner.ACTIVE_FACTORS)
    assert scoring.upserts[0]["available_sources"] == ["PRICE"]
    assert "FUNDAMENTAL" in scoring.upserts[0]["missing_sources"]

    # 재무 미연동 단계: 가용 팩터 2개 → 확신도 상한 B → DB 매핑 MEDIUM
    assert {u["source_agreement"] for u in scoring.upserts} <= {"MEDIUM"}
    assert json.loads(scoring.upserts[0]["score_breakdown"])["confidence"] == "B"


def test_run_daily_scores_spread_across_percentiles(fakes):
    analysis, scoring = fakes
    pool = FakePool(make_mock_rows())

    asyncio.run(runner.run_daily(pool))

    scores = [u["overall_score"] for u in scoring.upserts]
    assert max(scores) - min(scores) >= 50  # 백분위가 실제로 퍼져야 횡단면 점수


def test_run_daily_empty_db_is_graceful(fakes):
    stats = asyncio.run(runner.run_daily(FakePool([])))
    assert stats == {"analysis_date": None, "scored": 0, "withheld": 0, "universe": 0}


def test_scorecard_from_db_row_roundtrip(fakes):
    analysis, scoring = fakes
    pool = FakePool(make_mock_rows())
    asyncio.run(runner.run_daily(pool))

    stored = scoring.upserts[0]
    db_row = {
        "ticker": TICKERS[0],
        "score_breakdown": stored["score_breakdown"],
        "source_agreement": stored["source_agreement"],
        "warning": None,
        "disclaimer": None,
        "analysis_date": date.today(),
    }
    card = runner.scorecard_from_db_row(db_row)
    breakdown = json.loads(stored["score_breakdown"])
    assert card.score == breakdown["score"]
    assert card.confidence == breakdown["confidence"]
    assert card.drivers == breakdown["drivers"]
    assert card.warning and card.disclaimer  # None이어도 기본 고지문 복원
