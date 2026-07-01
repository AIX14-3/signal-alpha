import unittest

from app.agents.base import SourceAgentInput
from app.agents.report.agent import ReportAnalysisAgent


class FakeEmbedder:
    model = "fake-embed"

    async def embed(self, text):
        return [0.1] * 768


class FakeLLM:
    model = "gemini-fake"

    def __init__(self, payload):
        self._payload = payload
        self.prompt = None

    async def generate_json(self, prompt):
        self.prompt = prompt
        return self._payload


def _retriever_returning(rows):
    async def _retrieve(connection, *, stock_id, query, embedder, top_k=5):
        return list(rows)

    return _retrieve


def _input(direction="positive", score=0.5, stock_id=1, quant=None):
    return SourceAgentInput(
        source="REPORT",
        stock_code="005930",
        stock_id=stock_id,
        context={
            "direction": direction,
            "source_score": score,
            "report_quant": quant or {"valuation": {"methodology": "PER"}},
        },
    )


class ReportAnalysisAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_echoes_score_and_adds_grounded_evidence(self):
        rows = [{"chunk_text": "HBM 수요 견조", "raw_document_id": 42, "broker": "신한", "publish_date": "2026-06-24"}]
        payload = {
            "summary": "실적 개선 근거가 확인됩니다.",
            "risk_flags": ["demand_slowdown"],
            "evidence": [
                {"point": "HBM 수요 견조", "raw_document_id": 42, "broker": "신한", "publish_date": "2026-06-24"}
            ],
            "needs_review": False,
        }
        agent = ReportAnalysisAgent(
            connection=object(), embedder=FakeEmbedder(), llm=FakeLLM(payload),
            retriever=_retriever_returning(rows),
        )

        out = await agent.analyze(_input(direction="positive", score=0.5))

        self.assertEqual(out.source, "REPORT")
        self.assertEqual(out.direction, "positive")  # echoed, not computed
        self.assertEqual(out.score, 0.5)  # echoed
        self.assertEqual(out.analysis_source, "llm")
        self.assertEqual(out.prompt_ver, "report-rag-v1")
        self.assertEqual(out.llm_model, "gemini-fake")
        self.assertFalse(out.needs_review)
        self.assertEqual(out.summary, "실적 개선 근거가 확인됩니다.")
        rag = out.method_detail["report_rag"]
        self.assertEqual(rag["status"], "ok")
        self.assertEqual(rag["evidence"][0]["raw_document_id"], 42)

    async def test_fallback_when_no_chunks(self):
        agent = ReportAnalysisAgent(
            connection=object(), embedder=FakeEmbedder(), llm=FakeLLM({"summary": "x"}),
            retriever=_retriever_returning([]),
        )

        out = await agent.analyze(_input(direction="neutral", score=0.0, quant={}))

        self.assertTrue(out.needs_review)
        self.assertEqual(out.analysis_source, "rules")
        self.assertEqual(out.method_detail["report_rag"]["status"], "no_evidence")
        self.assertEqual(out.llm_error, "no_report_chunks")
        self.assertEqual(out.direction, "neutral")  # echo preserved on fallback
        self.assertEqual(out.score, 0.0)

    async def test_fallback_when_stock_id_missing(self):
        agent = ReportAnalysisAgent(
            connection=object(), embedder=FakeEmbedder(), llm=FakeLLM({"summary": "x"}),
            retriever=_retriever_returning([{"chunk_text": "c", "raw_document_id": 1}]),
        )

        out = await agent.analyze(_input(direction="positive", score=0.3, stock_id=None))

        self.assertEqual(out.method_detail["report_rag"]["status"], "deps_unavailable")
        self.assertTrue(out.needs_review)
        self.assertEqual(out.score, 0.3)

    async def test_guardrail_rejects_advice_language(self):
        rows = [{"chunk_text": "c", "raw_document_id": 42, "broker": "신한", "publish_date": "2026-06-24"}]
        payload = {"summary": "목표가 상향, 매수 추천드립니다.", "risk_flags": [], "evidence": [], "needs_review": False}
        agent = ReportAnalysisAgent(
            connection=object(), embedder=FakeEmbedder(), llm=FakeLLM(payload),
            retriever=_retriever_returning(rows),
        )

        out = await agent.analyze(_input(direction="positive", score=1.0, quant={}))

        self.assertEqual(out.method_detail["report_rag"]["status"], "advice_language_rejected")
        self.assertEqual(out.llm_error, "advice_language_rejected")
        self.assertEqual(out.analysis_source, "rules")
        self.assertEqual(out.score, 1.0)  # score untouched by rejection
        self.assertTrue(out.needs_review)


if __name__ == "__main__":
    unittest.main()
