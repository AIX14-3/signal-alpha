from __future__ import annotations

from typing import Any, Mapping

from app.analyzers.report.valuation import build_valuation_summary


_CONFIRMED = "confirmed"
_NOT_CONFIRMED = "not_confirmed"


def build_valuation_backtest_case_from_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a captured report pipeline sample into a valuation backtest case."""
    raw_documents = _by_id(sample.get("raw_documents") or [])
    raw_details = _by_raw_document_id(sample.get("report_raw_details") or [])
    valuation_facts = [
        _valuation_fact_from_sample(fact, raw_documents, raw_details)
        for fact in sample.get("report_valuation_facts") or []
    ]

    return {
        "case_id": sample.get("case_id"),
        "sample_origin": "collection_pipeline_sample",
        "ticker": sample.get("ticker"),
        "stock_name": sample.get("stock_name"),
        "signal_date": sample.get("signal_date"),
        "expected_outcome": sample.get("expected_outcome"),
        "valuation_facts": valuation_facts,
        "followup_observations": list(sample.get("followup_observations") or []),
    }


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


def _valuation_fact_from_sample(
    fact: Mapping[str, Any],
    raw_documents: dict[Any, Mapping[str, Any]],
    raw_details: dict[Any, Mapping[str, Any]],
) -> dict[str, Any]:
    raw_document_id = fact.get("raw_document_id")
    raw_document = raw_documents.get(raw_document_id, {})
    detail = raw_details.get(raw_document_id, {})

    return {
        "raw_document_id": raw_document_id,
        "stock_id": fact.get("stock_id") or detail.get("stock_id") or raw_document.get("stock_id"),
        "ticker": fact.get("ticker") or detail.get("ticker") or raw_document.get("ticker"),
        "broker": fact.get("broker") or detail.get("broker") or raw_document.get("provider"),
        "publish_date": fact.get("publish_date") or detail.get("publish_date") or raw_document.get("published_at"),
        "target_price": fact.get("target_price") or detail.get("target_price"),
        "forward_eps_est": fact.get("forward_eps_est"),
        "eps_fy": fact.get("eps_fy"),
        "methodology": fact.get("methodology") or "unknown",
        "applied_multiple": fact.get("applied_multiple"),
        "implied_multiple": fact.get("implied_multiple"),
        "peer_group": list(fact.get("peer_group") or []),
        "category_tag": fact.get("category_tag"),
        "rerating_thesis": fact.get("rerating_thesis"),
        "extraction_source": fact.get("extraction_source"),
        "needs_review": bool(fact.get("needs_review", True)),
    }


def _by_id(rows: list[Any]) -> dict[Any, Mapping[str, Any]]:
    return {row.get("id"): row for row in rows if isinstance(row, Mapping) and row.get("id") is not None}


def _by_raw_document_id(rows: list[Any]) -> dict[Any, Mapping[str, Any]]:
    return {
        row.get("raw_document_id"): row
        for row in rows
        if isinstance(row, Mapping) and row.get("raw_document_id") is not None
    }
