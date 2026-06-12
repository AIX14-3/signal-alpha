"""QUANT 라우트 — 묵업 풀/러너로 API 계약 검증."""

from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient

import app.api.routes.quant as quant_route
from app.core.database import get_database_pool
from app.main import app


class FakeRowPool:
    """GET 경로용 — ScoringRepository.get_latest_quant_score의 fetchrow에 응답."""

    def __init__(self, row) -> None:
        self.row = row

    async def fetchrow(self, sql: str, *args):
        return self.row


def make_db_row() -> dict:
    return {
        "ticker": "005930",
        "score_breakdown": json.dumps(
            {
                "score": 78,
                "confidence": "B",
                "drivers": ["단기반전 +", "저변동성 -", "마진개선 결측"],
                "calibration": {
                    "horizon_days": 20,
                    "median_excess_return": "-0.7%",
                    "p25_p75": ["-4.5%", "+3.9%"],
                    "sample_size": 25615,
                },
                "n_factors_used": 2,
                "z": {"reversal_1m": 0.51, "lowvol_60": -0.42, "quality_margin_yoy": None},
            },
            ensure_ascii=False,
        ),
        "source_agreement": "B",
        "warning": None,
        "disclaimer": None,
        "analysis_date": date(2026, 6, 12),
    }


def test_get_score_returns_scorecard_json():
    app.dependency_overrides[get_database_pool] = lambda: FakeRowPool(make_db_row())
    try:
        response = TestClient(app).get("/internal/quant/score/005930")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "005930"
    assert body["score"] == 78
    assert body["confidence"] == "B"
    assert body["calibration"]["horizon_days"] == 20
    assert body["analysis_date"] == "2026-06-12"
    # 법적 정책: 모든 응답에 warning + disclaimer
    assert body["warning"] and body["disclaimer"]


def test_get_score_404_when_absent():
    app.dependency_overrides[get_database_pool] = lambda: FakeRowPool(None)
    try:
        response = TestClient(app).get("/internal/quant/score/999999")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_run_daily_route_delegates_to_runner(monkeypatch):
    async def fake_run_daily(pool):
        return {"analysis_date": "2026-06-12", "scored": 8, "withheld": 0, "universe": 8}

    monkeypatch.setattr(quant_route, "run_daily", fake_run_daily)
    app.dependency_overrides[get_database_pool] = lambda: object()
    try:
        response = TestClient(app).post("/internal/quant/run-daily")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["scored"] == 8
