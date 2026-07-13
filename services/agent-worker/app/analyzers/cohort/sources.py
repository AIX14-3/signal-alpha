"""코호트 채점 대상 6소스의 스펙 테이블 — run_key·debate_method·PIT 키·로더 구성.

``analyzers/registry.py`` 를 확장하지 않고 별도 테이블로 두는 이유:
``SourceRegistration`` 은 순수 ``Analyzer`` 구현을 요구하는데 DART/REPORT 는 그런
구현이 없고, ``build_registry()`` 기본 목록에 끼워 넣으면 등록 전체를 도는 기존
경로(``AlternativeAnalyzeTaskHandler(connection)`` ad-hoc 사용)가 깨진다. 코호트
경로가 필요한 것은 (로더, run_key, debate_method, PIT date_key) 조회뿐이다.

run_key / debate_method 는 **기존 레인의 값을 그대로** 쓴다 — 집계 fan-in
(``list_latest_source_results_for_stock``) 이 run_key LIKE 프리픽스로 소스를
식별하고 소스별 최신 1행을 고르므로, 같은 키로 쓰면 집계는 무변경으로 집어간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analyzers.config import DataLabRuleConfig, PatentRuleConfig

# 시계열 압축(evidence.py)이 최근 60일 + 직전 12개월 월평균만 쓰므로 400일 창이면 덮는다.
# 예외: PATENT 의 filings_by_year 는 창 전체를 쓰는 장기 축이라 연구 러너와 같은 3000일.
COHORT_LOOKBACK_DAYS = 400
PATENT_HISTORY_DAYS = 3000


@dataclass(frozen=True)
class CohortSourceSpec:
    source: str
    # 기존 레인과 동일한 값 — 집계 fan-in 이 무변경으로 집어가는 조건.
    run_key: str
    debate_method: str
    # pit_rows() 의 known_at 컬럼 (app/ml/source_features.KNOWN_AT 과 동일 기준).
    date_key: str
    # RawEvidence.metadata 에서 정규화 행을 꺼낼 키. HIRING 은 일별 집계("rows") 대신
    # 공고별 원행("postings") — LLM 에겐 공고 제목이 실질 증거다.
    metadata_key: str = "rows"
    # REPORT 만 목표주가 대비 괴리 판단에 현재가가 필요하다.
    needs_close: bool = False

    def build_loader(self, *, repository: Any, connection: Any) -> Any:
        if self.source == "REPORT":
            from app.evidence_loaders.report_loader import ReportEvidenceLoader

            return ReportEvidenceLoader(connection)
        if self.source == "PRICE":
            from app.evidence_loaders.price_loader import PriceEvidenceLoader

            return PriceEvidenceLoader(connection)
        if self.source == "DATALAB":
            from app.evidence_loaders.datalab_loader import DataLabEvidenceLoader

            cfg = DataLabRuleConfig.from_env()
            return DataLabEvidenceLoader(
                repository,
                lookback_days=COHORT_LOOKBACK_DAYS,
                attention_window_days=cfg.attention_window_days,
            )
        if self.source == "HIRING":
            from app.evidence_loaders.hiring_loader import HiringEvidenceLoader

            return HiringEvidenceLoader(repository, lookback_days=COHORT_LOOKBACK_DAYS)
        if self.source == "PATENT":
            from app.evidence_loaders.patent_loader import PatentEvidenceLoader

            cfg = PatentRuleConfig.from_env()
            return PatentEvidenceLoader(
                repository, lookback_days=max(cfg.lookback_days, PATENT_HISTORY_DAYS)
            )
        if self.source == "DART":
            from app.evidence_loaders.dart_loader import DartEvidenceLoader

            return DartEvidenceLoader(repository, lookback_days=COHORT_LOOKBACK_DAYS)
        raise KeyError(f"unknown cohort source: {self.source!r}")


# debate_method 는 각 기존 레인이 쓰는 코드 그대로 (agent_results CHECK D-1..D-5;
# 레인마다 run_key 가 달라 (result_id, debate_method) 유니크 키는 충돌하지 않는다):
#   HIRING=D-1·PATENT=D-2·DATALAB=D-3 (analyzers/registry.DEBATE_METHODS)
#   DART=D-1 (orchestrator/dart/tasks.py) · REPORT=D-1 (orchestrator/report/tasks.py)
#   PRICE=D-4 (orchestrator/price/tasks.PRICE_DEBATE_METHOD)
COHORT_SOURCES: dict[str, CohortSourceSpec] = {
    "HIRING": CohortSourceSpec(
        source="HIRING", run_key="HIRING", debate_method="D-1",
        date_key="observed_date", metadata_key="postings",
    ),
    "PATENT": CohortSourceSpec(
        source="PATENT", run_key="PATENT", debate_method="D-2",
        date_key="application_date",
    ),
    "DATALAB": CohortSourceSpec(
        source="DATALAB", run_key="DATALAB", debate_method="D-3",
        date_key="observed_date",
    ),
    "DART": CohortSourceSpec(
        source="DART", run_key="DART", debate_method="D-1",
        date_key="report_date",
    ),
    "REPORT": CohortSourceSpec(
        source="REPORT", run_key="REPORT", debate_method="D-1",
        date_key="publish_date", needs_close=True,
    ),
    "PRICE": CohortSourceSpec(
        source="PRICE", run_key="PRICE", debate_method="D-4",
        date_key="trade_date",
    ),
}
