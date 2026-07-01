from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_split_collection_schedule_migration_seeds_source_specific_rows():
    migration = ROOT / "migrations" / "20260701_1500_split_collection_schedules.sql"
    content = migration.read_text(encoding="utf-8")

    for schedule_name in (
        "price-collection",
        "dart-collection",
        "report-collection",
        "alternative-collection",
    ):
        assert schedule_name in content

    assert "UPDATE public.collection_schedules" in content
    assert "daily-collection" in content
    assert "enabled = false" in content
    assert "ON CONFLICT (name) DO UPDATE" in content
