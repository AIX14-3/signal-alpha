import json
from pathlib import Path

from app.analyzers.report.valuation_backtest import evaluate_valuation_backtest_case


_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "report" / "valuation_backtest_cases.json"


def _load_cases():
    with _FIXTURE_PATH.open(encoding="utf-8") as file:
        return json.load(file)["cases"]


def test_report_valuation_backtest_fixture_contains_confirmed_and_not_confirmed_cases():
    cases = _load_cases()

    assert {case["expected_outcome"] for case in cases} == {"confirmed", "not_confirmed"}
    assert all(case["valuation_facts"] for case in cases)
    assert all(case["followup_observations"] for case in cases)


def test_report_valuation_backtest_cases_evaluate_expected_outcomes():
    results = [evaluate_valuation_backtest_case(case) for case in _load_cases()]

    assert all(result["expectation_matched"] is True for result in results)
    assert {result["outcome"] for result in results} == {"confirmed", "not_confirmed"}

    confirmed = next(result for result in results if result["outcome"] == "confirmed")
    assert confirmed["valuation"]["scenario_band"]["dispersion_level"] == "low"
    assert confirmed["confirmed_observation_count"] > confirmed["conflicted_observation_count"]

    not_confirmed = next(result for result in results if result["outcome"] == "not_confirmed")
    assert not_confirmed["valuation"]["needs_review"] is True
    assert "valuation_review_required" in not_confirmed["valuation"]["risk_flags"]
