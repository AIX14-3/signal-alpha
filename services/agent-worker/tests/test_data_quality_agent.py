"""데이터 품질 검증 에이전트/그래프 — 프로파일 이상 탐지와 LLM 검토 경로 계약."""

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents.validation import DataQualityAgent, ValidationGraphAgent, profile_rows

ASOF = date(2026, 7, 13)


def test_report_suspect_target_price_detected():
    # 실측된 오염: 목표주가 1·3·5원 파싱오류(20~25%). 1,000원 미만 = 파싱 산물.
    pit = [{"publish_date": "2026-07-01", "target_price": p} for p in [80000, 3, 90000, 5]]
    profile = profile_rows("REPORT", pit, ASOF)
    assert profile["suspect_target_price_rate"] == 0.5
    assert any("suspect_target_price" in a for a in profile["anomalies"])


def test_price_session_gap_detected():
    pit = [
        {"trade_date": "2026-06-01", "close": 100},
        {"trade_date": "2026-07-01", "close": 101},  # 30일 단절
    ]
    profile = profile_rows("PRICE", pit, ASOF)
    assert profile["max_gap_days"] == 30
    assert any("gap" in a for a in profile["anomalies"])


def test_datalab_zero_variance_detected():
    pit = [{"observed_date": f"2026-07-{d:02d}", "search_index": 50.0} for d in range(1, 12)]
    profile = profile_rows("DATALAB", pit, ASOF)
    assert "zero_variance_series" in profile["anomalies"]


def test_clean_profile_has_no_anomalies():
    pit = [
        {"publish_date": f"2026-07-{d:02d}", "target_price": 80000 + d}
        for d in range(1, 10)
    ]
    profile = profile_rows("REPORT", pit, ASOF)
    assert profile["anomalies"] == []


def test_graph_without_llm_uses_deterministic_profile_only():
    agent = ValidationGraphAgent(agent=DataQualityAgent(None))
    verdicts = asyncio.run(
        agent.validate(
            source="REPORT",
            asof=ASOF,
            pit_by_ticker={"005930": [{"publish_date": "2026-07-01", "target_price": 3}]},
        )
    )
    assert len(verdicts) == 1
    assert verdicts[0].ok is False
    assert verdicts[0].checked_by == "profile"


class _FakeClient:
    model = "fake-validator"

    def __init__(self, payload):
        self._payload = payload

    async def generate_json(self, prompt, schema=None):
        return self._payload


def test_graph_with_llm_merges_llm_issues():
    client = _FakeClient({
        "assessments": [{
            "ticker": "005930",
            "normalization_ok": True,
            "analysis_ok": False,
            "issues": ["근거가 데이터에 없는 사실을 인용"],
        }]
    })
    agent = ValidationGraphAgent(client=client)
    verdicts = asyncio.run(
        agent.validate(
            source="HIRING",
            asof=ASOF,
            pit_by_ticker={"005930": [{"observed_date": "2026-07-01"}] * 5},
            scored={"005930": {"score": 0.8, "evidence": ["대규모 채용"]}},
        )
    )
    assert verdicts[0].ok is False  # analysis_ok=False → 검토 승격
    assert verdicts[0].checked_by == "profile+llm"
    assert "근거가 데이터에 없는 사실을 인용" in verdicts[0].issues


def test_llm_failure_degrades_to_deterministic():
    class _Boom:
        model = "boom"

        async def generate_json(self, prompt, schema=None):
            raise RuntimeError("429")

    agent = DataQualityAgent(_Boom())
    verdicts = asyncio.run(
        agent.review(
            source="REPORT",
            asof=ASOF,
            profiles={"005930": {"anomalies": []}},
        )
    )
    assert verdicts[0].ok is True  # 검증 실패가 발행을 못 막는다 — 결정론 결과로 강등
