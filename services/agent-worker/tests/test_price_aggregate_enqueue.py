"""PriceAnalyzeTaskHandler: 주가 분석 후 SRC_INFER + AGGREGATE_SIGNAL 둘 다 인큐.

주가는 평일 매일 분석되므로 발행 하한선 — 대체데이터가 그날 없는 단독 종목도 발행되도록
PRICE 가 AGGREGATE_SIGNAL 을 직접 인큐한다(fan-in). 메타러너 SRC 라인도 함께 트리거한다.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.price.tasks import PriceAnalyzeTaskHandler
from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL, SRC_INFER


class _FakeQueue:
    def __init__(self):
        self.calls = []

    async def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return 100 + len(self.calls)


class _FakeAnalysisRepository:
    async def upsert_analysis_result(self, **kwargs):
        return {"id": 11}

    async def upsert_agent_result(self, **kwargs):
        return {"id": 22}


class _FakeReader:
    async def collect(self, stock_code):
        return SimpleNamespace(stock_code=stock_code)


class _FakeAgent:
    """계약 peer 스텁 — 핸들러가 SourceAnalysisAgent(analyze(input_data)) 를 구동함을 반영."""

    async def analyze(self, input_data):
        return SimpleNamespace(
            score=0.18,
            direction="positive",
            summary="5일 추세 상승",
            risk_flags=[],
            data_status="ok",
        )


def _build_handler():
    handler = PriceAnalyzeTaskHandler(connection=object())
    handler._reader = _FakeReader()
    handler._agent = _FakeAgent()
    handler._analysis_repository = _FakeAnalysisRepository()
    handler._queue = _FakeQueue()
    return handler


class PriceAnalyzeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_price_enqueues_both_src_infer_and_aggregate(self):
        handler = _build_handler()

        result = await handler(
            {
                "stock_id": 1,
                "task_context": {"stock_code": "005930", "signal_date": "2026-03-10"},
            }
        )

        task_types = [call["task_type"] for call in handler._queue.calls]
        self.assertIn(SRC_INFER, task_types)
        self.assertIn(AGGREGATE_SIGNAL, task_types)
        self.assertIsNotNone(result["src_infer_task_id"])
        self.assertIsNotNone(result["aggregate_task_id"])

        # AGGREGATE 는 (stock_id, signal_date) fan-in 으로 모든 소스를 모은다.
        aggregate_call = next(c for c in handler._queue.calls if c["task_type"] == AGGREGATE_SIGNAL)
        self.assertEqual(aggregate_call["task_context"]["signal_date"], "2026-03-10")
        self.assertTrue(aggregate_call["dedupe"])

    async def test_priority_propagates_to_aggregate(self):
        handler = _build_handler()

        await handler(
            {
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "signal_date": "2026-03-10",
                    "priority": "immediate",
                },
            }
        )

        aggregate_call = next(c for c in handler._queue.calls if c["task_type"] == AGGREGATE_SIGNAL)
        self.assertEqual(aggregate_call["priority"], "immediate")


if __name__ == "__main__":
    unittest.main()
