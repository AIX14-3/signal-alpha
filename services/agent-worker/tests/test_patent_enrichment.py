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

    async def list_unenriched_patent_details(self, *, limit):
        return self._rows[:limit]

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


if __name__ == "__main__":
    unittest.main()
