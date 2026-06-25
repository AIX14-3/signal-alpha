import json
import unittest

from app.agents.base import SourceAgentInput
from app.agents.report.agent import (
    COVERAGE,
    QUESTIONS,
    ReportAnalysisAgent,
    parse_report_llm_response,
)

_GOOD_JSON = json.dumps(
    {
        "direction": "positive",
        "score": 72,
        "summary": "목표주가 상향과 업황 호조로 긍정적.",
        "key_rationale": ["목표주가 9만원", "HBM 수요 견조"],
        "risk_flags": ["메모리 가격 변동"],
        "needs_review": False,
        "confidence": 65,
    },
    ensure_ascii=False,
)


class FakeRetriever:
    def __init__(self, chunks=None):
        self.calls = []
        self._chunks = chunks if chunks is not None else [
            {"chunk_text": "목표주가 9만원 상향", "raw_document_id": 1, "chunk_index": 0, "similarity": 0.9},
            {"chunk_text": "HBM 업황 호조", "raw_document_id": 1, "chunk_index": 2, "similarity": 0.8},
        ]

    async def __call__(self, stock_id, query, top_k=3):
        self.calls.append((stock_id, query, top_k))
        return list(self._chunks)


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    async def complete(self, *, prompt, model, timeout_seconds):
        self.prompts.append(prompt)
        return self.text


def _input(stock_id=1):
    return SourceAgentInput(
        source="REPORT",
        stock_code="005930",
        stock_id=stock_id,
        context={"report_quant": {"avg_target": 90000, "conflict_detected": False}},
    )


class ReportAnalysisAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_llm_path_returns_parsed_output_with_coverage(self):
        retriever = FakeRetriever()
        llm = FakeLlm(_GOOD_JSON)
        agent = ReportAnalysisAgent(retriever=retriever, llm_client=llm, llm_model="gpt-x")

        out = await agent.analyze(_input())

        self.assertEqual(out.source, "REPORT")
        self.assertEqual(out.direction, "positive")
        self.assertEqual(out.score, 72.0)
        self.assertEqual(out.analysis_source, "llm")
        self.assertFalse(out.needs_review)
        # 다중 질의: QUESTIONS 개수만큼 검색 호출
        self.assertEqual(len(retriever.calls), len(QUESTIONS))
        # 중복 (raw_document_id, chunk_index) 제거 → 2건
        self.assertEqual(len(out.method_detail["evidence_chunks"]), 2)
        self.assertEqual(out.method_detail["coverage"], COVERAGE)
        self.assertEqual(out.method_detail["report_quant"]["avg_target"], 90000)
        # 프롬프트에 3사 한계 note 포함
        self.assertIn(COVERAGE["note"], llm.prompts[0])

    async def test_no_llm_falls_back_to_partial(self):
        agent = ReportAnalysisAgent(retriever=FakeRetriever(), llm_client=None, llm_model=None)
        out = await agent.analyze(_input())
        self.assertEqual(out.direction, "unknown")
        self.assertEqual(out.data_status, "partial")
        self.assertEqual(out.analysis_source, "rules")
        self.assertTrue(out.needs_review)

    async def test_no_chunks_is_failed(self):
        agent = ReportAnalysisAgent(
            retriever=FakeRetriever(chunks=[]), llm_client=FakeLlm(_GOOD_JSON), llm_model="x"
        )
        out = await agent.analyze(_input())
        self.assertEqual(out.data_status, "failed")
        self.assertTrue(out.needs_review)
        self.assertIn("evidence_required", out.risk_flags)

    async def test_evidence_chunks_keep_traceable_report_metadata(self):
        chunks = [
            {
                "chunk_text": "target price raised",
                "raw_document_id": 11,
                "chunk_index": 0,
                "similarity": 0.91234,
                "title": "Samsung report",
                "source_url": "https://example.com/report.pdf",
                "securities_firm": "Test Securities",
                "publish_date": "2026-06-24",
            }
        ]
        agent = ReportAnalysisAgent(
            retriever=FakeRetriever(chunks=chunks), llm_client=None, llm_model=None
        )

        out = await agent.analyze(_input())

        self.assertEqual(
            out.method_detail["evidence_chunks"],
            [
                {
                    "raw_document_id": 11,
                    "chunk_index": 0,
                    "similarity": 0.9123,
                    "title": "Samsung report",
                    "source_url": "https://example.com/report.pdf",
                    "securities_firm": "Test Securities",
                    "publish_date": "2026-06-24",
                }
            ],
        )

    async def test_llm_prompt_contains_traceable_report_metadata(self):
        chunks = [
            {
                "chunk_text": "target price raised",
                "raw_document_id": 11,
                "chunk_index": 0,
                "similarity": 0.91,
                "title": "Samsung report",
                "source_url": "https://example.com/report.pdf",
                "securities_firm": "Test Securities",
                "publish_date": "2026-06-24",
            }
        ]
        llm = FakeLlm(_GOOD_JSON)
        agent = ReportAnalysisAgent(
            retriever=FakeRetriever(chunks=chunks), llm_client=llm, llm_model="x"
        )

        await agent.analyze(_input())

        self.assertIn('"title": "Samsung report"', llm.prompts[0])
        self.assertIn('"source_url": "https://example.com/report.pdf"', llm.prompts[0])
        self.assertIn('"securities_firm": "Test Securities"', llm.prompts[0])
        self.assertIn('"publish_date": "2026-06-24"', llm.prompts[0])

    async def test_missing_stock_id_is_failed(self):
        agent = ReportAnalysisAgent(
            retriever=FakeRetriever(), llm_client=FakeLlm(_GOOD_JSON), llm_model="x"
        )
        out = await agent.analyze(_input(stock_id=None))
        self.assertEqual(out.data_status, "failed")

    async def test_llm_exception_falls_back_with_error(self):
        class BoomLlm:
            async def complete(self, **kwargs):
                raise RuntimeError("429 rate limit")

        agent = ReportAnalysisAgent(retriever=FakeRetriever(), llm_client=BoomLlm(), llm_model="x")
        out = await agent.analyze(_input())
        self.assertEqual(out.analysis_source, "rules_fallback")
        self.assertEqual(out.data_status, "partial")
        self.assertEqual(out.llm_error, "429 rate limit")

    def test_parser_normalizes_bad_direction_and_probability_scores(self):
        parsed = parse_report_llm_response(
            '```json\n{"direction":"buy","summary":"x","score":80,"confidence":0.4}\n```'
        )
        self.assertEqual(parsed["direction"], "unknown")
        self.assertEqual(parsed["score"], 80.0)
        # confidence 0.4(확률)는 100점 척도로 환산
        self.assertEqual(parsed["confidence"], 40.0)

    def test_parser_keeps_low_integer_score_without_rescaling(self):
        # score=1은 '100점 중 1점'인 유효 값 → 100으로 반전되면 안 된다
        parsed = parse_report_llm_response('{"direction":"negative","summary":"x","score":1}')
        self.assertEqual(parsed["score"], 1.0)


if __name__ == "__main__":
    unittest.main()
