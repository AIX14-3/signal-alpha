"""PRICE 소스가 SourceAnalysisAgent 계약을 준수하는지 검증 (Tier C, 결정론).

``build_price_agent()`` 는 규칙 ``PriceAnalyzer`` 를 ``RuleSourceAgent`` 로 감싼 계약 peer 다.
점수/방향은 분석기 직접 호출과 동일해야 한다(투명 매핑).
"""

import unittest
from datetime import date
from typing import cast

from app.agents import SourceAgentInput, SourceAnalysisAgent
from app.agents.price import PRICE_PROMPT_VERSION, build_price_agent
from app.analyzers.price.analyzer import PriceAnalyzer

from tests.test_price_analyzer import make_evidence, make_rows


class PriceAnalysisAgentContractTest(unittest.IsolatedAsyncioTestCase):
    def test_build_price_agent_conforms_to_contract(self):
        agent = build_price_agent()
        self.assertIsInstance(agent, SourceAnalysisAgent)
        self.assertEqual(agent.source, "PRICE")
        self.assertEqual(agent.prompt_ver, PRICE_PROMPT_VERSION)
        self.assertEqual(PRICE_PROMPT_VERSION, "price-rules-v1")

    async def test_no_evidence_is_no_signal(self):
        agent = build_price_agent()

        output = await cast(SourceAnalysisAgent, agent).analyze(
            SourceAgentInput(source="PRICE", stock_code="005930")
        )

        self.assertEqual(output.source, "PRICE")
        self.assertEqual(output.direction, "unknown")
        self.assertEqual(output.data_status, "no_signal")
        # no_signal 은 review-required 상태가 아니다(partial/failed 와 구분).
        self.assertFalse(output.needs_review)
        self.assertEqual(output.analysis_source, "rules")
        self.assertIsNone(output.llm_model)

    async def test_uptrend_matches_direct_analyzer_call(self):
        closes = [100.0 + index for index in range(70)]
        rows = make_rows(closes, foreign=[100] * 70, institution=[50] * 70)
        evidence = make_evidence(rows)

        agent = build_price_agent()
        output = await agent.analyze(
            SourceAgentInput(
                source="PRICE",
                stock_code="005930",
                stock_id=1,
                analysis_date=date(2026, 3, 10),
                evidence=evidence,
            )
        )
        direct = await PriceAnalyzer().analyze("005930", evidence)

        # 계약 경유 결과가 분석기 직접 호출과 byte-identical (점수 소유=규칙).
        self.assertEqual(output.direction, "positive")
        self.assertGreater(output.score, 0.0)
        self.assertEqual(output.score, direct.score)
        self.assertEqual(output.direction, direct.direction)
        self.assertEqual(output.data_status, direct.data_status)
        self.assertEqual(output.analysis_source, "rules")


if __name__ == "__main__":
    unittest.main()
