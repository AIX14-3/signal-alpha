from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import migrate  # noqa: E402


def test_new_migration_template_warns_against_if_not_exists():
    original_migrations_dir = migrate.MIGRATIONS_DIR

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        migrate.MIGRATIONS_DIR = temp_path
        try:
            migrate.cmd_new("add audit field", target="backend")
        finally:
            migrate.MIGRATIONS_DIR = original_migrations_dir

        [created] = list(temp_path.glob("*_add_audit_field.sql"))
        content = created.read_text(encoding="utf-8")

    assert "-- target: backend" in content
    assert "IF NOT EXISTS를 쓰지 않는다" in content
    assert "ON CONFLICT / IF NOT EXISTS" not in content
    assert "시드 데이터가 필요하면 seeds/에 분리" in content
