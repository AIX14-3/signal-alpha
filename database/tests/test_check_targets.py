"""check_targets.py 의 target↔테이블·cross-DB FK 정합 검사 단위 테스트.

DB 불필요(순수 정적 분석). 실제 활성 마이그가 통과하는지 + H1 류(cross-DB FK,
target 불일치)가 잡히는지 + 주석/DO 블록 오탐이 없는지 확인한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

DB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DB_DIR / "tools"))
sys.path.insert(0, str(DB_DIR))

import check_targets as ct  # noqa: E402


def test_active_migrations_pass():
    """현재 활성 마이그 전체가 태그·정합 검사를 통과한다(H1 수정 반영)."""
    assert ct.check_tags(ct.MIGRATIONS_DIR) == []
    assert ct.check_migration_locality(ct.MIGRATIONS_DIR) == []


def test_seeds_have_targets():
    assert ct.check_tags(ct.SEEDS_DIR) == []


def test_cross_db_fk_detected():
    """analysis_requests(COLLECTION) → users(BACKEND) 는 cross-DB FK 위반."""
    sql = (
        "ALTER TABLE public.analysis_requests "
        "ADD CONSTRAINT fk FOREIGN KEY (user_id) REFERENCES public.users(id);"
    )
    subjects, edges = ct.analyze_sql(sql)
    assert subjects == {"analysis_requests"}
    assert ("analysis_requests", "users") in edges
    owner_dbs = ct._BUCKET_DBS[ct.bucket_of("analysis_requests")]
    ref_dbs = ct._BUCKET_DBS[ct.bucket_of("users")]
    assert not owner_dbs <= ref_dbs  # 위반


def test_backend_target_rejects_collection_table():
    """target=backend 인데 collection 전용 테이블을 건드리면 불일치."""
    target_dbs = ct._TARGET_DBS["backend"]
    coll_dbs = ct._BUCKET_DBS[ct.bucket_of("analysis_requests")]
    assert not target_dbs <= coll_dbs


def test_allowed_fks_pass():
    """backend→published, collection→published, published→published 는 허용."""
    for owner, ref in (
        ("watchlists", "stocks"),  # backend → published
        ("processing_queue", "stocks"),  # collection → published
        ("signal_events", "source_documents"),  # published → published
    ):
        owner_dbs = ct._BUCKET_DBS[ct.bucket_of(owner)]
        ref_dbs = ct._BUCKET_DBS[ct.bucket_of(ref)]
        assert owner_dbs <= ref_dbs, f"{owner}->{ref} should be allowed"


def test_comments_ignored():
    """주석 안의 테이블 언급은 파싱되지 않는다(오탐 방지)."""
    sql = (
        "-- ALTER TABLE public.analysis_requests REFERENCES public.users\n"
        "/* CREATE TABLE public.x REFERENCES public.users */\n"
        "SELECT 1;"
    )
    subjects, edges = ct.analyze_sql(sql)
    assert subjects == set()
    assert edges == []


def test_do_block_table_ddl_analyzed():
    """DO $$ ... $$ 안의 CREATE/ALTER TABLE·FK 도 검사 대상이다(멱등 패턴 누락 방지)."""
    sql = (
        "DO $$ BEGIN\n"
        "  IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename='foo') THEN\n"
        "    ALTER TABLE foo ADD CONSTRAINT c FOREIGN KEY (x) REFERENCES public.users(id);\n"
        "  END IF;\n"
        "END $$;"
    )
    subjects, edges = ct.analyze_sql(sql)
    assert "foo" in subjects
    assert ("foo", "users") in edges


def test_string_literal_does_not_break_parsing():
    """DEFAULT 문자열 안의 ;·-- 가 있어도 뒤따르는 REFERENCES 가 올바로 귀속된다."""
    sql = (
        "CREATE TABLE public.foo (\n"
        "  id bigint,\n"
        "  label text NOT NULL DEFAULT 'a;b--c',\n"
        "  owner_id bigint REFERENCES public.users(id)\n"
        ");"
    )
    subjects, edges = ct.analyze_sql(sql)
    assert subjects == {"foo"}
    assert ("foo", "users") in edges


def test_non_public_schema_qualifier():
    """public 외 스키마 한정자도 테이블명만 캡처한다."""
    subjects, edges = ct.analyze_sql("CREATE TABLE app.user_sessions (id bigint);")
    assert subjects == {"user_sessions"}
