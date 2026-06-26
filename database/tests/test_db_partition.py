"""db_partition 분류 로직 (#525/#531 WS-A) — 순수 단위테스트(DB 불필요)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db_partition  # noqa: E402


def test_backend_and_published_are_disjoint():
    assert not (db_partition.BACKEND_TABLES & db_partition.PUBLISHED_TABLES)


def test_backend_keep_is_union():
    assert db_partition.backend_keep() == (
        db_partition.BACKEND_TABLES | db_partition.PUBLISHED_TABLES
    )


def test_collection_only_excludes_backend_published_and_ledger():
    actual = {
        "users",  # backend
        "final_signals",  # published
        "schema_migrations",  # ledger
        "ml_inferences",  # collection
        "dart_corp_codes",  # collection
        "processing_queue",  # collection
    }
    assert db_partition.collection_only(actual) == {
        "ml_inferences",
        "dart_corp_codes",
        "processing_queue",
    }


def test_published_matches_read_model_join_tables():
    # api.signals_current / api.signal_detail 가 JOIN 하는 테이블과 일치.
    assert db_partition.PUBLISHED_TABLES == {
        "stocks",
        "final_signals",
        "analysis_results",
        "agent_results",
        "signal_events",
        "source_documents",
    }


def test_validate_flags_missing_declared_table():
    # 선언된 backend 테이블이 실제 DB 에 없으면 점검에 잡힌다.
    issues = db_partition.validate({"final_signals", "stocks"})
    assert any("없는 테이블" in m for m in issues)


def test_validate_clean_when_all_present():
    actual = db_partition.backend_keep() | {"ml_inferences", "schema_migrations"}
    assert db_partition.validate(actual) == []
