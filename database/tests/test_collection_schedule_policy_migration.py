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
