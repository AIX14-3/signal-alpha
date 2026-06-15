import unittest
from datetime import date

from app.aggregator import AlternativeAggregator
from app.analyzers.config import AggregatorConfig
from app.analyzers.registry import SourceRegistration
from app.orchestrator.alternative_agent import AlternativeAgent
from app.schemas.source_result import SourceResult

CONFIG = AggregatorConfig(
    weights={"HIRING": 0.34, "PATENT": 0.33, "DATALAB": 0.33},
    positive_threshold=0.2,
    negative_threshold=-0.2,
    confidence_base=0.3,
    confidence_per_source=0.35,
)


class _Conn:
    pass


class _Acquire:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def acquire(self):
        return _Acquire()


class FakeLoader:
    def __init__(self, *, raises=False):
        self._raises = raises

    async def load(self, *, stock_id, stock_code, as_of):
        if self._raises:
            raise RuntimeError("loader boom")
        return []


class FakeAnalyzer:
    def __init__(self, source, result):
        self.source = source
        self._result = result

    async def analyze(self, stock_code, evidence):
        return self._result


def _registration(source, *, result, raises=False):
    return SourceRegistration(
        source=source,
        debate_method="D-1",
        analyzer=FakeAnalyzer(source, result),
        loader_factory=lambda repo, raises=raises: FakeLoader(raises=raises),
    )


class AlternativeAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_failing_source_is_isolated(self):
        good = SourceResult("PATENT", "005930", "positive", 0.6, "ok")
        regs = [
            _registration("PATENT", result=good),
            _registration("DATALAB", result=good, raises=True),
        ]
        agent = AlternativeAgent(
            registrations=regs,
            aggregator=AlternativeAggregator(CONFIG),
            repository_factory=lambda conn: conn,
        )

        signal = await agent.run(
            pool=FakePool(),
            stock_id=1,
            stock_code="005930",
            as_of=date(2026, 6, 11),
        )

        self.assertEqual(signal.available_sources, ["PATENT"])
        self.assertEqual(signal.missing_sources, ["DATALAB"])
        self.assertEqual(signal.direction, "positive")
        self.assertIn("analyzer_error", signal.risk_flags)
        self.assertEqual(signal.per_source["DATALAB"].data_status, "failed")


if __name__ == "__main__":
    unittest.main()
