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
