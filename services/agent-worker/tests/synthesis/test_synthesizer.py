"""synthesizer: 응답 파싱(설명 필드만) + 투자조언 차단 + LLM 클라이언트 경유."""

from __future__ import annotations

import json
import unittest

import pytest

from app.synthesis.synthesizer import (
    RiskNarrative,
    SynthesisError,
    Synthesizer,
    parse_synthesis_response,
)


def test_parse_valid_response() -> None:
    text = json.dumps(
        {
            "headline": "혼조 신호",
            "narrative": "여러 소스가 엇갈립니다.",
            "key_points": ["DART 실적 개선", "거래량 증가"],
            "caution_points": ["소스 불일치"],
        }
    )
    narrative = parse_synthesis_response(text)
    assert isinstance(narrative, RiskNarrative)
    assert narrative.headline == "혼조 신호"
    assert narrative.key_points == ["DART 실적 개선", "거래량 증가"]


def test_parse_strips_code_fence() -> None:
    text = "```json\n{\"headline\":\"h\",\"narrative\":\"n\"}\n```"
    narrative = parse_synthesis_response(text)
    assert narrative.headline == "h"
    assert narrative.caution_points == []


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(SynthesisError):
        parse_synthesis_response("not json")


def test_parse_requires_headline_and_narrative() -> None:
    with pytest.raises(SynthesisError):
        parse_synthesis_response(json.dumps({"headline": "only"}))


def test_parse_rejects_investment_advice_english() -> None:
    text = json.dumps({"headline": "h", "narrative": "You should buy this stock."})
    with pytest.raises(SynthesisError):
        parse_synthesis_response(text)


def test_parse_rejects_investment_advice_korean() -> None:
    text = json.dumps(
        {"headline": "h", "narrative": "n", "caution_points": ["지금 매수 추천드립니다"]}
    )
    with pytest.raises(SynthesisError):
        parse_synthesis_response(text)


def test_parse_rejects_directive_advice_phrases() -> None:
    # 지시(directive) 표현은 차단.
    for phrase in ["지금 매수하세요", "매도 추천", "비중 확대 권장", "적극 매수", "담으세요"]:
        text = json.dumps({"headline": "h", "narrative": phrase})
        with pytest.raises(SynthesisError):
            parse_synthesis_response(text)


def test_parse_allows_descriptive_market_terms() -> None:
    # 서술적 시장 표현(순매도/매수세 등)은 통과 — 더 이상 매수/매도 부분 문자열로 차단 안 함.
    for phrase in ["외국인 순매도가 지속되었습니다.", "기관 매수세가 유입되었습니다.", "매도 우위 흐름."]:
        text = json.dumps({"headline": "시장 동향", "narrative": phrase})
        narrative = parse_synthesis_response(text)
        assert narrative.narrative == phrase


def test_parse_allows_factual_target_price_statements() -> None:
    """REPORT 는 주제 자체가 목표주가다 — 사실 서술은 통과해야 한다.

    과거 필터는 "목표가"/"보유"를 맨 부분문자열로 막아 REPORT 의 **모든 정상 출력**과 지분
    공시 서술("5% 이상 보유")까지 거부했다. directive-only 로 전환한 뒤의 회귀 가드.
    """
    for phrase in [
        "증권사 목표주가가 12만원으로 상향 조정되었습니다.",
        "목표가 대비 현재가 괴리율은 18%입니다.",
        "국민연금이 지분 5% 이상을 보유하고 있습니다.",
        "자기주식 취득 후 보유 물량이 늘었습니다.",
    ]:
        text = json.dumps({"headline": "리포트 동향", "narrative": phrase})
        narrative = parse_synthesis_response(text)
        assert narrative.narrative == phrase


class _FakeClient:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        self.calls += 1
        return self._response


class SynthesizerClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_synthesize_calls_client_and_parses(self):
        client = _FakeClient(
            json.dumps({"headline": "h", "narrative": "n", "key_points": ["k"]})
        )
        synth = Synthesizer(client=client, model="test-model")

        narrative = await synth.synthesize({"signal": "positive", "evidence": []})

        assert client.calls == 1
        assert narrative.headline == "h"
        assert narrative.key_points == ["k"]


if __name__ == "__main__":
    unittest.main()
