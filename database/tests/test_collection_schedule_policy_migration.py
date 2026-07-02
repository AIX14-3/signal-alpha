from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_collection_schedule_policy_migration_adds_scheduler_knobs():
    migration = ROOT / "migrations" / "20260702_0900_collection_schedule_policy.sql"
    content = migration.read_text(encoding="utf-8")

    assert "ALTER TABLE public.collection_schedules" in content
    assert "report_limit" in content
    assert "report_days_back" in content
    assert "report_max_pages" in content
    assert "alternative_collect_enabled" in content
    assert "alternative_analyze_enabled" in content
    assert "backpressure_max_waiting" in content
    assert "collection_schedules_report_limit_check" in content
    assert "collection_schedules_backpressure_max_failed_check" in content


def test_schema_snapshot_contains_scheduler_policy_columns():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")

    assert "report_limit integer DEFAULT 100 NOT NULL" in schema
    assert "report_days_back integer DEFAULT 7 NOT NULL" in schema
    assert "report_max_pages integer DEFAULT 20 NOT NULL" in schema
    assert "alternative_collect_enabled boolean DEFAULT true NOT NULL" in schema
    assert "alternative_analyze_timeout_seconds integer DEFAULT 3600 NOT NULL" in schema
    assert "backpressure_max_waiting integer" in schema
    assert "collection_schedules_alternative_analyze_timeout_check" in schema
    assert "collection_schedules_report_max_pages_check" in schema
