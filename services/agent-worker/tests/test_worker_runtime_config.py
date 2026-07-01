from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_compose_worker_defaults_match_split_topology():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "QUEUE_DRAIN_DAEMON_ENABLED: ${QUEUE_DRAIN_DAEMON_ENABLED:-true}" in compose
    assert "PRICE_COLLECTOR_ENABLED: ${PRICE_COLLECTOR_ENABLED:-false}" in compose
    assert "SYNTHESIS_LLM_MODEL: ${SYNTHESIS_LLM_MODEL:-gemini-2.5-flash}" in compose
    assert "INTERNAL_API_TOKEN: ${INTERNAL_API_TOKEN:?INTERNAL_API_TOKEN is required" in compose


def test_compose_main_server_env_file_is_optional_for_config_validation():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "path: ./services/main-server/.env" in compose
    assert "required: false" in compose


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


def test_worker_runbooks_match_current_queue_and_auth_contracts():
    schedule = (ROOT / "docs" / "runbooks" / "agent-pipeline-schedule.md").read_text(
        encoding="utf-8"
    )
    report_smoke = (
        ROOT / "docs" / "runbooks" / "report-pipeline-smoke-test.md"
    ).read_text(encoding="utf-8")
    gcp = (ROOT / "docs" / "gcp-deploy-runbook.md").read_text(encoding="utf-8")
    staging = (ROOT / "docs" / "pre-deploy-staging-rehearsal-runbook.md").read_text(
        encoding="utf-8"
    )

    assert "Queue cycle execution | `POST /internal/queue/run-cycle`" in schedule
    assert "DB-backed scheduler agent" in schedule
    assert "run_scheduler_instance.py" in schedule
    assert "Price collection trigger | `POST /internal/price/collect`" in schedule
    assert "Report collection enqueue | `POST /internal/schedules/report/collect`" in schedule
    assert "Alternative target | local collector/analyzer CLIs" in schedule
    assert "`alternative` target runs Patent/DataLab collection" in schedule
    assert "Hiring collection remains the dedicated hiring crawler CronJob" in schedule
    assert "publish_signals" in schedule
    assert "ml_infer" not in schedule
    assert "meta_combine" not in schedule
    assert "risk_veto" not in schedule
    assert "/internal/queue/$task/run-batch" not in schedule
    assert "X-Internal-Token: $INTERNAL_API_TOKEN" in schedule
    assert "internal/tasks/collect_report/enqueue" in report_smoke
    assert 'Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}' in report_smoke
    assert "INTERNAL_API_TOKEN=" in gcp
    assert "SYNTHESIS_LLM_MODEL=gemini-2.5-flash" in staging
    assert "gemini-2.0-flash" not in staging
    assert "runtime.publishing" in schedule
    assert "single_db_noop" in gcp
    assert "PUBLISH_SIGNALS tasks are skipped" in gcp


def test_worker_specs_match_current_queue_auth_and_model_contracts():
    dart_spec = (ROOT / "docs" / "spec" / "dart-collector-analyzer-spec.md").read_text(
        encoding="utf-8"
    )
    report_gemini = (
        ROOT / "docs" / "spec" / "report-gemini-pdf-parsing-dev-guide.md"
    ).read_text(encoding="utf-8")
    report_state = (
        ROOT / "docs" / "spec" / "report-rag-current-state.md"
    ).read_text(encoding="utf-8")

    assert "gemini-2.0-flash" not in report_gemini
    assert "REPORT_LLM_MODEL=gemini-2.5-flash" in report_gemini
    assert "X-Internal-Token" in dart_spec
    assert "X-Internal-Token" in report_gemini
    assert "X-Internal-Token" in report_state
    assert "/internal/queue/run-cycle" in dart_spec
    assert "/internal/queue/run-cycle" in report_gemini
    assert "/internal/queue/run-cycle" in report_state
    assert "/internal/queue/normalize_dart/run-batch" not in dart_spec
    assert "/internal/queue/analyze_dart/run-batch" not in dart_spec
    assert "/internal/queue/collect_report/run-batch" not in report_gemini
    assert "/internal/queue/process_report/run-batch" not in report_gemini
    assert "/internal/queue/normalize_report/run-batch" not in report_gemini
    assert "/internal/queue/analyze_report/run-batch" not in report_gemini
    assert "/internal/queue/collect_report/run-batch" not in report_state
    assert "/internal/queue/process_report/run-batch" not in report_state
    assert "/internal/queue/normalize_report/run-batch" not in report_state
    assert "/internal/queue/analyze_report/run-batch" not in report_state
