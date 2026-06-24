from pathlib import Path

from app.main import app


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
