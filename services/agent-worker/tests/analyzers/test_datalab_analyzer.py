"""DataLab analyzer — Phase 0 (#525): 결정론 판정 제거, 피처 산출만.

판정/점수는 더 이상 내지 않는다(학습형 메타러너 return 채널이 산출). 분석기는 피처를 계산하고
``direction="unknown"`` / ``score=0`` / ``data_status="no_signal"`` 로 반환해 AGGREGATE 점수·방향
집계에서 빠진다(커버리지로만 노출). 피처 자체는 ``compute_indicators`` 단위테스트가 별도로 검증한다.
"""

import unittest
from datetime import date, timedelta

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab import DataLabAnalyzer
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 11)
CONFIG = DataLabRuleConfig(
    lookback_days=30,
    min_observations=5,
    momentum_threshold=0.1,
    spike_threshold=0.2,
    stale_days=14,
    momentum_weight=0.6,
    spike_weight=0.2,
    change_weight=0.2,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _row(
    days_ago,
    search_index,
    *,
    is_spike=False,
    change_pct=None,
    weight=1.0,
    polarity="demand",
    polarity_source="default",
    polarity_model=None,
):
    return {
        "category_id": 1,
        "weight": weight,
        "keyword": "k",
        "keyword_group": "g",
        "observed_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "search_index": search_index,
        "previous_search_index": None,
        "change_pct": change_pct,
        "is_spike": is_spike,
        "polarity": polarity,
        "polarity_source": polarity_source,
        "polarity_model": polarity_model,
    }


def _evidence(rows):
    return [
        RawEvidence(
            source="DATALAB",
            stock_code="005930",
            title="t",
            content="",
            metadata={"rows": rows, "as_of": AS_OF.isoformat(), "lookback_days": 30},
        )
    ]


_RISING = [
    _row(2, 70), _row(4, 70), _row(6, 70),
    _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
]


class DataLabAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_data_is_no_signal(self):
        # 행이 없으면 no_signal + no_data flag (변경 없음).
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.data_status, "no_signal")
        self.assertIn("no_data", result.risk_flags)

    async def test_rows_present_is_feature_only_no_verdict(self):
        # 강한 상승 검색 트렌드여도 판정을 내지 않는다 — unknown/0/no_signal.
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(_RISING))
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.data_status, "no_signal")
        self.assertEqual(result.risk_flags, [])
        self.assertIn("피처 산출", result.summary)

    async def test_risk_keyword_rows_still_no_verdict(self):
        # 위험 키워드(리콜/불매) 상승도 결정론 negative 판정을 내지 않는다.
        rows = [
            _row(2, 80, polarity="risk"), _row(4, 80, polarity="risk"), _row(6, 80, polarity="risk"),
            _row(18, 40, polarity="risk"), _row(20, 40, polarity="risk"), _row(22, 40, polarity="risk"),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)

    async def test_llm_polarity_sets_provenance(self):
        # LLM 분류 provenance 는 판정과 무관하게 그대로 노출(llm_model + summary 공개).
        rows = [
            _row(2, 70, polarity_source="llm", polarity_model="gemini-2.5-flash-lite"),
            _row(4, 70, polarity_source="llm", polarity_model="gemini-2.5-flash-lite"),
            _row(6, 70),
            _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.llm_model, "gemini-2.5-flash-lite")
        self.assertIn("LLM 분류 키워드 2건", result.summary)


if __name__ == "__main__":
    unittest.main()
