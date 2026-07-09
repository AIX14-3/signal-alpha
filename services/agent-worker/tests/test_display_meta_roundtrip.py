"""표시 전용 메타(PATENT patent_meta / HIRING hiring_meta)가 에이전트 왕복에서 살아남는지.

회귀 배경: ``RuleSourceAgent._to_output`` 과 소스별 에이전트의 ``_base_detail`` 이 두 필드를
method_detail 에 싣지 않아, ``_from_output`` 으로 복원된 SourceResult 에서 조용히 사라졌다.
persistence 는 그 SourceResult 만 보므로 score_breakdown 에 영영 도달하지 못했다
(실측: 발행 280건 중 PATENT.patent 키 0건). 점수·방향은 이 필드와 무관하므로 테스트도
"값이 보존되는가"만 본다.
"""

import unittest

from app.agents import RuleSourceAgent, SourceAgentInput
from app.orchestrator.alternative.tasks import _from_output
from app.orchestrator.alternative_persistence import _method_detail
from app.schemas.source_result import HiringMeta, PatentMeta, SourceResult

_POSTING = {
    "job_title": "2026 계약학과 모집",
    "posting_date": "2026-04-24",
    "closing_date": "2026-05-08",
    "closing_date_display": "2026-05-08",
    "is_always_open": False,
    "source_url": "https://jasoseol.com/recruit/103790",
}
_PUBLICATION = {
    "application_no": "1020230001234",
    "title": "배터리 셀 냉각 구조",
    "application_date": "2023-01-05",
    "publication_date": "2024-07-10",
    "tech_category": "H01M",
}


class _StubAnalyzer:
    def __init__(self, result: SourceResult) -> None:
        self.source = result.source
        self._result = result

    async def analyze(self, stock_code, evidence):  # noqa: ANN001
        return self._result


def _hiring_result() -> SourceResult:
    return SourceResult(
        source="HIRING",
        stock_code="005380",
        direction="unknown",
        score=0.0,
        summary="최근 90일 채용공고 2건.",
        data_status="no_signal",
        hiring_meta=HiringMeta(postings=[dict(_POSTING)]),
    )


def _patent_result() -> SourceResult:
    return SourceResult(
        source="PATENT",
        stock_code="005930",
        direction="positive",
        score=0.51,
        summary="최근 900일 공개 특허 300건.",
        data_status="ok",
        patent_meta=PatentMeta(
            recent_publications=[dict(_PUBLICATION)],
            filing_trend=[{"year": 2024, "count": 12}],
        ),
    )


class DisplayMetaRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def _round_trip(self, result: SourceResult) -> SourceResult:
        agent = RuleSourceAgent(_StubAnalyzer(result))
        output = await agent.analyze(
            SourceAgentInput(source=result.source, stock_code=result.stock_code)
        )
        return _from_output(output)

    async def test_hiring_meta_survives_agent_round_trip(self):
        restored = await self._round_trip(_hiring_result())
        self.assertIsNotNone(restored.hiring_meta)
        assert restored.hiring_meta is not None
        self.assertEqual(restored.hiring_meta.postings, [_POSTING])

    async def test_patent_meta_survives_agent_round_trip(self):
        restored = await self._round_trip(_patent_result())
        self.assertIsNotNone(restored.patent_meta)
        assert restored.patent_meta is not None
        self.assertEqual(restored.patent_meta.recent_publications, [_PUBLICATION])
        self.assertEqual(restored.patent_meta.filing_trend, [{"year": 2024, "count": 12}])

    async def test_round_trip_keeps_score_and_direction_unchanged(self):
        # 표시 전용 필드를 실어도 점수 경로는 불변이어야 한다.
        original = _patent_result()
        restored = await self._round_trip(original)
        self.assertEqual(restored.direction, original.direction)
        self.assertEqual(restored.score, original.score)
        self.assertEqual(restored.data_status, original.data_status)

    async def test_persistence_writes_meta_into_method_detail(self):
        # persistence 는 복원된 SourceResult 만 본다 — 여기까지 와야 score_breakdown 에 실린다.
        hiring = await self._round_trip(_hiring_result())
        detail = _method_detail(hiring)
        self.assertEqual(detail["hiring"], {"postings": [_POSTING]})

        patent = await self._round_trip(_patent_result())
        detail = _method_detail(patent)
        self.assertEqual(detail["patent"]["recent_publications"], [_PUBLICATION])

    async def test_sources_without_meta_keep_method_detail_shape(self):
        # 값이 없는 소스는 키 자체가 생기지 않아야 한다(기존 계약 유지).
        plain = SourceResult(
            source="DATALAB",
            stock_code="005930",
            direction="positive",
            score=0.3,
            summary="검색량 증가.",
            data_status="ok",
        )
        restored = await self._round_trip(plain)
        self.assertIsNone(restored.hiring_meta)
        self.assertIsNone(restored.patent_meta)
        detail = _method_detail(restored)
        self.assertNotIn("hiring", detail)
        self.assertNotIn("patent", detail)


if __name__ == "__main__":
    unittest.main()
