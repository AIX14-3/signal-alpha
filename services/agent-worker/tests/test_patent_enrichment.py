"""Unit tests for patent LLM enrichment (C3) — pure logic + orchestration.

DB and Gemini are faked; these lock the feature contract (clamping, stage
coercion, abstract extraction) and the status transitions the analyzer relies on.
"""

import json
import unittest

from app.enrichment.patent_features import (
    PatentEnricher,
    abstract_of,
    build_prompt,
    validate_features,
)


class AbstractOfTests(unittest.TestCase):
    def test_reads_astrtcont_from_dict(self):
        self.assertEqual(abstract_of({"astrtCont": " 본 발명은… "}), "본 발명은…")

    def test_reads_from_json_string_payload(self):
        # JSONB comes back from asyncpg as a string (no codec registered).
        self.assertEqual(abstract_of(json.dumps({"abstract": "AAA"})), "AAA")

    def test_missing_abstract_is_empty(self):
        self.assertEqual(abstract_of({"foo": "bar"}), "")
        self.assertEqual(abstract_of(None), "")


class ValidateFeaturesTests(unittest.TestCase):
    def test_clamps_and_keeps_stage(self):
        out = validate_features(
            {
                "significance": 1.7,
                "core_business_relevance": -0.3,
                "novelty": "0.5",
                "commercialization_stage": "DEVELOPMENT",
                "rationale": "근거",
            }
        )
        self.assertEqual(out["significance"], 1.0)
        self.assertEqual(out["core_business_relevance"], 0.0)
        self.assertEqual(out["novelty"], 0.5)
        self.assertEqual(out["commercialization_stage"], "development")

    def test_unknown_stage_and_bad_numbers_default(self):
        out = validate_features({"significance": "abc", "commercialization_stage": "shipping"})
        self.assertEqual(out["significance"], 0.0)
        self.assertEqual(out["commercialization_stage"], "unknown")

    def test_non_dict_rejected(self):
        with self.assertRaises(ValueError):
            validate_features(["not", "a", "dict"])

    def test_rationale_truncated(self):
        out = validate_features({"significance": 0.5, "rationale": "가" * 500})
        self.assertEqual(len(out["rationale"]), 300)


class BuildPromptTests(unittest.TestCase):
    def test_includes_title_and_abstract(self):
        prompt = build_prompt("반도체 패키징", "본 발명은 적층 구조…")
        self.assertIn("반도체 패키징", prompt)
        self.assertIn("본 발명은 적층 구조", prompt)
        self.assertIn("significance", prompt)


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.last_raw_document_ids = "unset"

    async def list_unenriched_patent_details(self, *, limit, raw_document_ids=None):
        self.last_raw_document_ids = raw_document_ids
        rows = self._rows
        if raw_document_ids is not None:
            wanted = set(raw_document_ids)
            rows = [r for r in rows if r["raw_document_id"] in wanted]
        return rows[:limit]

    async def update_patent_llm_features(self, *, raw_document_id, features, status):
        self.updates.append((raw_document_id, features, status))


class _FakeClient:
    def __init__(self, *, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    async def generate_json(self, prompt):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class EnricherRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_caches_features(self):
        repo = _FakeRepo([{"raw_document_id": 1, "patent_title": "T", "extra_payload": {"astrtCont": "A"}}])
        client = _FakeClient(result={"significance": 0.8, "commercialization_stage": "near_market"})
        stats = await PatentEnricher(repo, client).run()
        self.assertEqual(stats["success"], 1)
        rid, features, status = repo.updates[0]
        self.assertEqual((rid, status), (1, "success"))
        self.assertEqual(features["significance"], 0.8)

    async def test_no_title_or_abstract_is_skipped_without_calling_llm(self):
        repo = _FakeRepo([{"raw_document_id": 2, "patent_title": "", "extra_payload": {}}])
        client = _FakeClient(result={"significance": 0.9})
        stats = await PatentEnricher(repo, client).run()
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(client.calls, 0)
        self.assertEqual(repo.updates[0], (2, None, "skipped"))

    async def test_llm_error_marks_failed(self):
        repo = _FakeRepo([{"raw_document_id": 3, "patent_title": "T", "extra_payload": {"astrtCont": "A"}}])
        client = _FakeClient(error=RuntimeError("boom"))
        stats = await PatentEnricher(repo, client).run()
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(repo.updates[0], (3, None, "failed"))

    async def test_raw_document_ids_scopes_the_worklist(self):
        # Two pending patents; the ENRICH_PATENT path enriches only the named id.
        repo = _FakeRepo(
            [
                {"raw_document_id": 10, "patent_title": "A", "extra_payload": {"astrtCont": "a"}},
                {"raw_document_id": 11, "patent_title": "B", "extra_payload": {"astrtCont": "b"}},
            ]
        )
        client = _FakeClient(result={"significance": 0.5})
        stats = await PatentEnricher(repo, client, raw_document_ids=[10]).run()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(repo.last_raw_document_ids, [10])
        self.assertEqual([u[0] for u in repo.updates], [10])


class _MissingColumnError(Exception):
    """Stand-in for asyncpg.exceptions.UndefinedColumnError (SQLSTATE 42703)."""

    sqlstate = "42703"


class _StaleSchemaConnection:
    """Fake asyncpg connection: first SELECT/UPDATE referencing the llm_* columns
    raises 42703; the no-llm fallback query (or a no-op) succeeds.

    Detection is by SQL text containing ``llm_`` so the fallback variants — which
    only emit ``NULL AS llm_features`` — are treated as healthy.
    """

    def __init__(self):
        self.fetch_calls = []
        self.execute_calls = []

    @staticmethod
    def _references_real_llm_column(sql: str) -> bool:
        # The fallback selects literal "NULL AS llm_features"; real refs read p.llm_*.
        return "p.llm_features" in sql or "p.llm_status" in sql or "llm_status = " in sql

    async def fetch(self, sql, *args):
        self.fetch_calls.append(sql)
        if self._references_real_llm_column(sql):
            raise _MissingColumnError("column p.llm_status does not exist")
        return [{"raw_document_id": 1, "llm_features": None, "llm_status": None}]

    async def execute(self, sql, *args):
        self.execute_calls.append(sql)
        if self._references_real_llm_column(sql):
            raise _MissingColumnError("column llm_status does not exist")
        return "UPDATE 1"


class StalePatentSchemaGuardTests(unittest.IsolatedAsyncioTestCase):
    """L1 guard: patent LLM methods degrade gracefully when prod lacks the
    migration-019 ``llm_features`` / ``llm_status`` columns (SQLSTATE 42703)."""

    def _repo(self, conn):
        import sys

        sys.path.insert(0, "packages/data-access")
        from signal_alpha_data_access.repositories.raw_details import RawDetailRepository

        return RawDetailRepository(conn)

    async def test_list_by_stock_falls_back_with_null_placeholders(self):
        conn = _StaleSchemaConnection()
        rows = await self._repo(conn).list_patent_details_by_stock(stock_id=7)
        # Two fetches: the real query (42703) then the fallback that succeeds.
        self.assertEqual(len(conn.fetch_calls), 2)
        self.assertEqual(rows[0]["llm_features"], None)
        self.assertEqual(rows[0]["llm_status"], None)

    async def test_list_unenriched_returns_empty_batch(self):
        conn = _StaleSchemaConnection()
        rows = await self._repo(conn).list_unenriched_patent_details(limit=50)
        self.assertEqual(rows, [])

    async def test_update_is_noop_when_columns_missing(self):
        conn = _StaleSchemaConnection()
        # Must not raise; the cache write is silently skipped.
        await self._repo(conn).update_patent_llm_features(
            raw_document_id=3, features={"significance": 0.5}, status="success"
        )
        self.assertEqual(len(conn.execute_calls), 1)

    async def test_non_42703_errors_still_propagate(self):
        class _OtherError(Exception):
            sqlstate = "08006"  # connection_failure — not a missing column

        class _Conn:
            async def fetch(self, sql, *args):
                raise _OtherError("boom")

        with self.assertRaises(_OtherError):
            await self._repo(_Conn()).list_patent_details_by_stock(stock_id=1)


if __name__ == "__main__":
    unittest.main()
