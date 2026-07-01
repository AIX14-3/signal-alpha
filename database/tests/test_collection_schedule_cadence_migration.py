from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_collection_schedule_cadence_migration_adds_repeat_fields():
    migration = ROOT / "migrations" / "20260701_1600_collection_schedule_cadence.sql"
    content = migration.read_text(encoding="utf-8")

    assert "ALTER TABLE public.collection_schedules" in content
    assert "frequency_minutes" in content
    assert "active_from_local" in content
    assert "active_until_local" in content
    assert "collection_schedules_frequency_minutes_check" in content
    assert "price-collection" in content
    assert "dart-collection" in content
    assert "report-collection" in content
    assert "alternative-collection" in content
