"""DataLab analyzer — 메타러너 폐기(2026-07) 후 결정론 verdict 복원.

#525 "피처 전용"은 학습형 메타러너가 판정을 대신한다는 전제였으나 그 채널이 폐기됐다. 이제 DataLab 도
특허·DART·리포트와 동일하게 ``rules.evaluate_indicators`` 로 방향/점수를 내고 AGGREGATE 등가중 통합
점수에 산입된다. 데이터가 없거나 표본이 너무 작으면(guard) no_signal/neutral 로 정직하게 남는다.
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

    async def test_rows_present_produce_verdict(self):
        # 강한 상승 검색 트렌드(최근 70 > 직전 50, +40%, prior 5건) → 결정론 positive verdict.
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(_RISING))
        self.assertEqual(result.direction, "positive")
        self.assertGreater(result.score, 0.0)
        self.assertEqual(result.data_status, "ok")
        self.assertIn("방향", result.summary)
        self.assertNotIn("피처 산출", result.summary)

    async def test_small_prior_sample_is_guarded_neutral(self):
        # 이전 구간 관측이 min_prior_observations 미만이면 low_base 가드로 모멘텀 억제 →
        # 팬텀 신호 없이 neutral/0(단, data_status 는 partial 로 점수엔 남는다).
        rows = [
            _row(2, 80, polarity="risk"), _row(4, 80, polarity="risk"), _row(6, 80, polarity="risk"),
            _row(18, 40, polarity="risk"), _row(20, 40, polarity="risk"), _row(22, 40, polarity="risk"),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.score, 0.0)
        self.assertIn("low_base", result.risk_flags)
        self.assertEqual(result.data_status, "partial")

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
