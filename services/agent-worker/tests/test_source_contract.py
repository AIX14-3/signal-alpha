"""소스 베이스라인 출력 계약 검증기 conformance 테스트.

대체데이터 베이스라인이 맞춰야 하는 method_detail 계약을 핀으로 박아, 사용자가 5개 소스
베이스라인을 만들 때 self-check 의 기준이 흔들리지 않게 한다.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.aggregation.source_contract import (
    is_contract_compliant,
    method_detail_warnings,
    validate_source_method_detail,
)

_GOOD = {
    "source": "HIRING",
    "source_score": 0.32,
    "direction": "positive",
    "data_status": "ok",
    "summary": "채용 공고 MoM +40%",
    "risk_flags": [],
}


class SourceContractTest(unittest.TestCase):
    def test_fully_conforming_detail_passes(self):
        self.assertEqual(validate_source_method_detail(_GOOD), [])
        self.assertTrue(is_contract_compliant(_GOOD))

    def test_non_dict_fails(self):
        self.assertTrue(validate_source_method_detail("nope"))
        self.assertTrue(validate_source_method_detail(None))

    def test_missing_source_is_violation(self):
        detail = {k: v for k, v in _GOOD.items() if k != "source"}
        violations = validate_source_method_detail(detail)
        self.assertTrue(any("source" in v for v in violations))

    def test_source_type_alias_resolves(self):
        # 대체 트리오(HIRING/PATENT/DATALAB)와 source_type 별칭도 해석된다.
        detail = {**_GOOD, "source": None, "source_type": "DATALAB"}
        self.assertEqual(validate_source_method_detail(detail), [])

    def test_invalid_direction_is_violation(self):
        violations = validate_source_method_detail({**_GOOD, "direction": "bullish"})
        self.assertTrue(any("direction" in v for v in violations))

    def test_missing_score_is_violation_unless_method_score_fallback(self):
        detail = {k: v for k, v in _GOOD.items() if k != "source_score"}
        self.assertTrue(validate_source_method_detail(detail))
        # method_score 폴백이 있으면 통과.
        self.assertEqual(
            validate_source_method_detail(detail, method_score=70), []
        )

    def test_source_score_out_of_range_is_violation(self):
        violations = validate_source_method_detail({**_GOOD, "source_score": 1.8})
        self.assertTrue(any("range" in v for v in violations))

    def test_invalid_data_status_is_violation(self):
        violations = validate_source_method_detail({**_GOOD, "data_status": "weird"})
        self.assertTrue(any("data_status" in v for v in violations))

    def test_risk_flags_must_be_list(self):
        violations = validate_source_method_detail({**_GOOD, "risk_flags": "high"})
        self.assertTrue(any("risk_flags" in v for v in violations))

    def test_price_handler_shape_conforms(self):
        # price/tasks.py 가 실제로 쓰는 method_detail 형태(direction 은 method_signal 로 전달).
        price_detail = {
            "source": "PRICE",
            "source_score": 0.18,
            "summary": "5일 추세 상승",
            "risk_flags": [],
            "data_status": "ok",
        }
        self.assertEqual(
            validate_source_method_detail(price_detail, method_signal="positive"), []
        )

    def test_warnings_flag_missing_summary_and_direction(self):
        warnings = method_detail_warnings({"source": "PATENT", "source_score": 0.1})
        self.assertTrue(any("summary" in w for w in warnings))
        self.assertTrue(any("direction" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
