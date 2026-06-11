import unittest

from app.analyzers.dart.financials import extract_dart_financial_metrics


class DartFinancialMetricExtractionTest(unittest.TestCase):
    def test_extracts_korean_financial_metrics_as_krw_million(self):
        metrics = extract_dart_financial_metrics(
            "매출액 77,781억원, 영업이익 6,606억원, 당기순이익 5,745억원을 기록했다."
        )

        self.assertEqual(
            metrics,
            [
                {"metric_name": "dart_revenue", "metric_value": 7778100, "metric_unit": "KRW_million"},
                {
                    "metric_name": "dart_operating_profit",
                    "metric_value": 660600,
                    "metric_unit": "KRW_million",
                },
                {"metric_name": "dart_net_income", "metric_value": 574500, "metric_unit": "KRW_million"},
            ],
        )

    def test_ignores_text_without_financial_metrics(self):
        self.assertEqual(extract_dart_financial_metrics("대표이사 변경 공시입니다."), [])


if __name__ == "__main__":
    unittest.main()
