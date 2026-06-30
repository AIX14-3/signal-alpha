from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analyzers.dart.llm import DartLlmAnalysisError, parse_dart_llm_response
from app.narrate.base import NarrateError, reject_advice
from app.policy_safety import POLICY_RECOMMENDATION_PHRASES
from app.synthesis.synthesizer import SynthesisError, parse_synthesis_response


PROMPT_PATHS = (
    Path("app/prompts/synthesis_v1.md"),
    Path("app/prompts/dart_analysis_v1.md"),
    Path("app/prompts/dart_narrate_v1.md"),
    Path("app/prompts/price_narrate_v1.md"),
    Path("app/prompts/report_narrate_v1.md"),
)


@pytest.mark.parametrize("phrase", POLICY_RECOMMENDATION_PHRASES)
def test_worker_runtime_filters_reject_policy_recommendation_phrases(phrase: str):
    synthesis_payload = json.dumps(
        {"headline": "h", "narrative": phrase, "key_points": [], "caution_points": []},
        ensure_ascii=False,
    )
    with pytest.raises(SynthesisError):
        parse_synthesis_response(synthesis_payload)

    with pytest.raises(NarrateError):
        reject_advice([phrase])

    dart_payload = json.dumps(
        {
            "direction": "neutral",
            "score": 0,
            "summary": phrase,
            "key_facts": [],
            "risk_flags": [],
            "needs_review": False,
            "confidence": 80,
        },
        ensure_ascii=False,
    )
    with pytest.raises(DartLlmAnalysisError):
        parse_dart_llm_response(dart_payload)


@pytest.mark.parametrize("prompt_path", PROMPT_PATHS)
def test_worker_prompts_name_policy_recommendation_phrases(prompt_path: Path):
    text = prompt_path.read_text(encoding="utf-8")

    for phrase in POLICY_RECOMMENDATION_PHRASES:
        assert phrase in text
