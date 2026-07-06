from pathlib import Path
import re

from app.main import app


ROOT = Path(__file__).resolve().parents[3]


def test_legacy_report_agent_routes_are_not_registered():
    paths = {route.path for route in app.routes}

    assert "/agents/report" not in paths
    assert "/agents/analyze" not in paths


def test_legacy_report_runtime_files_are_removed():
    service_root = Path(__file__).resolve().parents[1]

    legacy_files = [
        service_root / "app" / "api" / "routes" / "report.py",
        service_root / "app" / "collectors" / "report" / "collector.py",
        service_root / "app" / "analyzers" / "report" / "analyzer.py",
        service_root / "app" / "collectors" / "report" / "parsers" / "vector_store.py",
    ]

    assert [path for path in legacy_files if path.exists()] == []


def test_runtime_code_does_not_reference_legacy_report_tables():
    legacy_table_patterns = {
        "report_raw": re.compile(r"\breport_raw\b"),
        "report_signal": re.compile(r"\breport_signal\b"),
    }
    search_roots = [
        ROOT / "services" / "agent-worker" / "app",
        ROOT / "packages" / "data-access" / "signal_alpha_data_access",
    ]
    offenders: list[str] = []

    for search_root in search_roots:
        for path in search_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for table, pattern in legacy_table_patterns.items():
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(ROOT)} references {table}")

    assert offenders == []


def test_database_docs_do_not_claim_report_legacy_is_kept_for_runtime_code():
    docs = [
        ROOT / "database" / "AGENTS.md",
        ROOT / "database" / "docs" / "migration_rules.md",
        ROOT / "database" / "docs" / "table_descriptions.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)

    assert "기존 코드 때문에 임시로 유지" not in combined
    assert "raw_documents` → `report_raw_details` → `report_chunks" not in combined
    # report_raw/report_signal 은 20260630_1200 마이그로 DROP 됨 — 문서가 이를 반영해야 하고,
    # 더 이상 '추후 DROP 준비를 위해 보존' 으로 서술하지 않는다.
    assert "20260630_1200_drop_legacy_report_raw_signal" in combined
    assert "추후 DROP migration 준비" not in combined


def test_report_contract_docs_do_not_reintroduce_rag_runtime_contract():
    docs = [
        ROOT / "services" / "agent-worker" / "AGENTS.md",
        ROOT / "database" / "README.md",
        ROOT / "database" / "docs" / "table_descriptions.md",
        ROOT / "database" / "erd" / "signal_alpha_core_erd.md",
        ROOT / "docs" / "data-pipeline.md",
        ROOT / "docs" / "glossary.md",
        ROOT / "docs" / "spec" / "report-rag-current-state.md",
        ROOT / "docs" / "db-migration-conventions.md",
        ROOT / "docs" / "spec" / "data-layers-l2-l10-spec.md",
        ROOT / "docs" / "spec" / "data-foundations-and-l1-l10-workflow.md",
        ROOT / "docs" / "spec" / "cross-layer-orchestration-and-risks.md",
        ROOT / "docs" / "spec" / "report-valuation-reinterpretation-strategy.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)

    forbidden_contracts = [
        "raw_documents -> report_raw_details -> report_chunks",
        "report_chunks` 테이블 제거",
        "report_chunks` 테이블·`embed_report`",
        "리포트 임베딩/RAG 는 7-에이전트화 Stage 0 에서 pgvector",
        "에이전트 배선은 Stage 1/2",
        "RAG 리콜 정확 보장",
        "RAG 복구 후보",
        "마이그레이션 전체에 vector 흔적 0",
        "기존 `report_chunks`",
        "ALTER TABLE report_chunks",
        "`report_chunks` 적재",
        "RAG 검색(report_chunks)",
        "RAG를 복구할 경우 `report_chunks`",
        "과거 유사 re-rating 사례 RAG",
    ]

    for phrase in forbidden_contracts:
        assert phrase not in combined

    assert "Report 런타임에서는 `report_chunks`를 적재하거나 조회하지 않습니다" in combined
