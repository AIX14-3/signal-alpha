"""5개 소스 베이스라인 출력 계약 정합 감사 — 회귀 가드.

각 소스 핸들러/분석기가 실제로 내는 method_detail(대표형)이 aggregation 계약을 만족하는지
핀으로 박는다. fixture 는 각 핸들러의 실제 구성과 일치시킨다:

  - PRICE    : app/orchestrator/price/tasks.py (실제 verdict, data_status=ok)
  - DART     : app/analyzers/dart/source_result.py + dart/tasks.py (#546 Phase 0, no_signal/unknown)
  - REPORT   : app/orchestrator/report/tasks.py (#525 Phase 0, no_signal/unknown + 밸류에이션)
  - HIRING/PATENT/DATALAB : app/orchestrator/alternative/tasks.py (signal 기반 direction/score)

감사 결론: 5개 베이스라인은 모두 계약을 만족한다. Phase 0 소스(DART/REPORT)는 direction="unknown"
+ data_status="no_signal" 의 정당한 features-only 계약을 쓴다.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.aggregation.source_contract import validate_source_method_detail

# --- 각 핸들러가 실제로 내는 method_detail 대표형 + 함께 전달되는 method_signal ---

PRICE_DETAIL = (
    {
        "source": "PRICE",
        "source_score": 0.18,
        "summary": "5일 추세 상승, 거래량 증가",
        "risk_flags": [],
        "data_status": "ok",
    },
    "positive",  # method_signal
)

DART_DETAIL = (  # #546 Phase 0: features-only
    {
        "source": "DART",
        "source_score": 0.0,
        "summary": "DART 공시 3건 피처 산출. 판정은 학습형 메타러너가 수행.",
        "risk_flags": ["correction_disclosure"],
        "needs_review": True,
        "data_status": "no_signal",
        "event_count": 3,
        "events": [],
        "analysis_source": "rules",
    },
    "unknown",
)

REPORT_DETAIL = (  # #525 Phase 0: features-only + 밸류에이션
    {
        "source": "REPORT",
        "source_score": 0.0,
        "summary": "증권사 리포트 2건 밸류에이션 피처 산출.",
        "risk_flags": [],
        "needs_review": False,
        "data_status": "no_signal",
        "analysis_source": "features",
        "report_quant": {"valuation": {"target_price": 90000, "needs_review": False}},
    },
    "unknown",
)

HIRING_DETAIL = (  # alternative 경로 — 실제 verdict
    {
        "source": "HIRING",
        "run_key": "HIRING",
        "direction": "positive",
        "score": 0.32,
        "data_status": "ok",
    },
    "positive",
)

DATALAB_NO_SIGNAL_DETAIL = (  # alternative 경로 — coverage-only 변형
    {
        "source": "DATALAB",
        "run_key": "DATALAB",
        "direction": "unknown",
        "score": 0.0,
        "data_status": "no_signal",
    },
    "unknown",
)

PATENT_DETAIL = (
    {
        "source": "PATENT",
        "run_key": "PATENT",
        "direction": "neutral",
        "score": 0.05,
        "data_status": "ok",
    },
    "neutral",
)

ALL_BASELINES = {
    "PRICE": PRICE_DETAIL,
    "DART": DART_DETAIL,
    "REPORT": REPORT_DETAIL,
    "HIRING": HIRING_DETAIL,
    "DATALAB": DATALAB_NO_SIGNAL_DETAIL,
    "PATENT": PATENT_DETAIL,
}


class SourceBaselineConformanceTest(unittest.TestCase):
    def test_all_five_baselines_conform(self):
        for name, (detail, method_signal) in ALL_BASELINES.items():
            with self.subTest(source=name):
                violations = validate_source_method_detail(
                    detail, method_signal=method_signal
                )
                self.assertEqual(violations, [], f"{name} 계약 위반: {violations}")

    def test_phase0_unknown_direction_is_accepted(self):
        # DART/REPORT 의 direction="unknown" + no_signal 이 위반이 아니어야 한다.
        for name in ("DART", "REPORT"):
            detail, sig = ALL_BASELINES[name]
            self.assertEqual(validate_source_method_detail(detail, method_signal=sig), [])

    def test_no_signal_without_score_is_exempt(self):
        # source_score 가 아예 없어도 no_signal 이면 점수 면제(coverage-only).
        detail = {"source": "DART", "data_status": "no_signal"}
        self.assertEqual(validate_source_method_detail(detail, method_signal="unknown"), [])

    def test_unknown_direction_still_rejected_when_scored_source_lacks_score(self):
        # data_status=ok 인데 score 가 전혀 없으면 여전히 위반(점수 면제 아님).
        detail = {"source": "HIRING", "direction": "positive", "data_status": "ok"}
        self.assertTrue(validate_source_method_detail(detail))


if __name__ == "__main__":
    unittest.main()
