from __future__ import annotations

from typing import Any, Mapping

from app.analyzers.report.valuation import build_valuation_summary


_CONFIRMED = "confirmed"
_NOT_CONFIRMED = "not_confirmed"


def evaluate_valuation_backtest_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a report valuation fixture against later data observations."""
    valuation = build_valuation_summary(case.get("valuation_facts") or [])
    observations = list(case.get("followup_observations") or [])
    confirmed_count = _count_observations(observations, _CONFIRMED)
    conflicted_count = _count_observations(observations, "conflicted")

    outcome = _NOT_CONFIRMED
    if not valuation["needs_review"] and confirmed_count > conflicted_count:
        outcome = _CONFIRMED

    expected_outcome = case.get("expected_outcome")
    return {
        "case_id": case.get("case_id"),
        "ticker": case.get("ticker"),
        "signal_date": case.get("signal_date"),
        "outcome": outcome,
        "expected_outcome": expected_outcome,
        "expectation_matched": outcome == expected_outcome,
        "confirmed_observation_count": confirmed_count,
        "conflicted_observation_count": conflicted_count,
        "valuation": valuation,
    }


def _count_observations(observations: list[Any], alignment: str) -> int:
    return sum(
        1
        for observation in observations
        if isinstance(observation, Mapping) and observation.get("alignment") == alignment
    )
