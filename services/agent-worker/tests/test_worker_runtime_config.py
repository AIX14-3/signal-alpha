from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_compose_worker_defaults_match_split_topology():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "QUEUE_DRAIN_DAEMON_ENABLED: ${QUEUE_DRAIN_DAEMON_ENABLED:-true}" in compose
    assert "PRICE_COLLECTOR_ENABLED: ${PRICE_COLLECTOR_ENABLED:-false}" in compose
    assert "SYNTHESIS_LLM_MODEL: ${SYNTHESIS_LLM_MODEL:-gemini-2.5-flash}" in compose


def test_env_example_avoids_removed_gemini_defaults():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "DART_LLM_MODEL=gemini-2.5-flash" in env_example
    assert "REPORT_LLM_MODEL=gemini-2.5-flash" in env_example
    assert "gemini-2.0-flash" not in env_example


def test_agent_worker_readme_matches_current_dart_analysis_contract():
    readme = (ROOT / "services" / "agent-worker" / "README.md").read_text(encoding="utf-8")

    assert "LangGraph-based" not in readme
    assert "DART LLM 판정 경로는 제거" in readme
    assert "DART_LLM_MODEL=gemini-2.0-flash" not in readme
