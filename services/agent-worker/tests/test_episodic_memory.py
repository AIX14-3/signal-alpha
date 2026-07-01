"""Episodic memory (Wave-3 Stage 2) — writer/recall round-trip + degrade tests.

Covers the plan's invariants:
  (c) memory writer/recall round-trip (store a situation, recall the nearest),
  (d) embedding failure → publishing continues (writer returns False, recall []),
  and that recalled memory is a *reference only* — it never edits the headline.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.memory import (
    EpisodeRecall,
    EpisodeWriter,
    SituationInput,
    build_situation_text,
)
from app.memory.episode_recall import RecalledEpisode
from app.orchestrator.aggregation.judge import build_memory_reference, judge_with_memory
from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler

from tests.test_final_signal_aggregator import FakeConnection, dart_agent_row


class FakeEmbedder:
    """Deterministic stand-in for GeminiEmbeddingClient. Maps text→a stable vector
    so the same situation always embeds identically (recall neighborhood is
    meaningful in-test without any network)."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("embedding backend down")
        # Cheap stable 4-dim vector keyed on the text (dim doesn't matter to the
        # fake repository; the real one asserts 768 at the client boundary).
        h = float(abs(hash(text)) % 1000) / 1000.0
        return [h, 1.0 - h, len(text) % 7 / 7.0, 0.5]


class FakeEpisodeRepository:
    """In-memory signal_episodes: upsert by natural key, recall by insertion order
    (nearest-neighbor is exercised at the SQL layer, not here)."""

    def __init__(self, seed=None):
        self.rows = list(seed or [])
        self.upserts = []

    async def upsert_episode(self, **kwargs):
        self.upserts.append(kwargs)
        self.rows.append(kwargs)
        return {"id": len(self.rows)}

    async def recall_similar(self, *, embedding, exclude_stock_id=None,
                             exclude_signal_date=None, limit=3):
        out = []
        for row in self.rows:
            if (exclude_stock_id is not None and row.get("stock_id") == exclude_stock_id
                    and row.get("signal_date") == exclude_signal_date):
                continue
            out.append({**row, "distance": 0.1})
            if len(out) >= limit:
                break
        return out


class SituationTextTest(unittest.TestCase):
    def test_writer_and_recall_embed_identical_text(self):
        breakdown = {
            "HIRING": {"direction": "positive", "score": 0.4, "data_status": "ok", "summary": "채용 급증 흔적"},
            "PATENT": {"direction": "negative", "score": -0.2, "data_status": "ok"},
            "PRICE": {"data_status": "missing"},
        }
        text = build_situation_text(SituationInput(signal="mixed", breakdown=breakdown))
        # missing source is dropped; running sources rendered; framing is 흔적/근거.
        self.assertIn("HIRING", text)
        self.assertIn("PATENT", text)
        self.assertNotIn("PRICE", text)
        self.assertIn("긍정 흔적", text)
        self.assertNotIn("매수", text)
        self.assertNotIn("confidence", text.lower())


class WriterRecallSignalConsistencyTest(unittest.IsolatedAsyncioTestCase):
    """K: writer 는 situation_signal(=aggregate 신호)로 임베딩하되 저장 direction 은
    발행 headline 을 유지한다. recall 도 aggregate 신호로 임베딩 → 텍스트 byte-identical
    (headline 이 aggregate 와 달라도 이웃이 유효)."""

    async def test_situation_signal_makes_writer_recall_text_identical(self):
        embedder = FakeEmbedder()
        repo = FakeEpisodeRepository()
        breakdown = {"HIRING": {"direction": "positive", "score": 0.4, "data_status": "ok"}}
        await EpisodeWriter(embedder=embedder, repository=repo).write(
            stock_id=1,
            signal_date="2026-07-01",
            run_key="AGGREGATED",
            signal="positive",  # 발행 headline (메타러너)
            final_score=0.6,
            breakdown=breakdown,
            situation_signal="mixed",  # aggregate 신호 (임베딩 텍스트용)
        )
        await EpisodeRecall(embedder=embedder, repository=repo).recall(
            signal="mixed",  # recall 은 aggregate 신호만 가용
            breakdown=breakdown,
            exclude_stock_id=2,
            exclude_signal_date="2026-07-02",
        )
        # writer·recall 이 동일 텍스트를 임베딩 → 코사인 이웃 유효(재현성).
        self.assertEqual(embedder.calls[0], embedder.calls[1])
        # 저장 direction 은 발행 headline (사후 outcome 분석용), aggregate 신호 아님.
        self.assertEqual(repo.upserts[0]["direction"], "positive")


class EpisodeRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_then_recall_round_trip(self):
        embedder = FakeEmbedder()
        repo = FakeEpisodeRepository()
        writer = EpisodeWriter(embedder=embedder, repository=repo)
        recall = EpisodeRecall(embedder=embedder, repository=repo)

        breakdown = {"HIRING": {"direction": "positive", "score": 0.4, "data_status": "ok"}}
        ok = await writer.write(
            stock_id=1, signal_date=date(2026, 6, 18), run_key="AGGREGATED",
            signal="positive", final_score=62.0, breakdown=breakdown,
        )
        self.assertTrue(ok)
        self.assertEqual(len(repo.upserts), 1)
        self.assertEqual(repo.upserts[0]["direction"], "positive")
        self.assertIn("HIRING", repo.upserts[0]["sources"])

        episodes = await recall.recall(
            signal="positive", breakdown=breakdown,
            exclude_stock_id=2, exclude_signal_date=date(2026, 6, 19),
        )
        self.assertEqual(len(episodes), 1)
        self.assertIsInstance(episodes[0], RecalledEpisode)
        self.assertEqual(episodes[0].direction, "positive")
        self.assertIsNotNone(episodes[0].similarity)

    async def test_writer_degrades_on_embedding_failure(self):
        writer = EpisodeWriter(embedder=FakeEmbedder(fail=True), repository=FakeEpisodeRepository())
        ok = await writer.write(
            stock_id=1, signal_date=date(2026, 6, 18), run_key="AGGREGATED",
            signal="positive", final_score=62.0, breakdown={},
        )
        self.assertFalse(ok)  # no raise → publishing continues

    async def test_recall_degrades_to_empty_on_failure(self):
        recall = EpisodeRecall(embedder=FakeEmbedder(fail=True), repository=FakeEpisodeRepository())
        episodes = await recall.recall(signal="positive", breakdown={})
        self.assertEqual(episodes, [])

    async def test_cold_start_returns_empty(self):
        recall = EpisodeRecall(embedder=FakeEmbedder(), repository=FakeEpisodeRepository())
        episodes = await recall.recall(signal="neutral", breakdown={})
        self.assertEqual(episodes, [])


class JudgeMemoryTest(unittest.TestCase):
    def test_memory_reference_never_changes_numbers(self):
        aggregate = {
            "signal": "positive", "final_score": 70.0, "consensus_score": 80.0,
            "source_agreement": "HIGH", "needs_review": False,
            "score_breakdown": {}, "positive_evidence": [], "caution_evidence": [],
            "risk_flags": [],
        }
        episodes = [
            RecalledEpisode(stock_id=9, signal_date=date(2026, 1, 1), run_key="AGGREGATED",
                            direction="positive", score=0.3, sources={}, outcome=None, distance=0.2),
        ]
        judged = judge_with_memory(aggregate, build_memory_reference(episodes))
        # numbers identical.
        self.assertEqual(judged["signal"], "positive")
        self.assertEqual(judged["final_score"], 70.0)
        self.assertEqual(judged["consensus_score"], 80.0)
        # reference attached.
        self.assertEqual(len(judged["memory_reference"]), 1)

    def test_adverse_past_outcome_raises_needs_review_only(self):
        aggregate = {
            "signal": "positive", "final_score": 70.0, "consensus_score": 80.0,
            "source_agreement": "HIGH", "needs_review": False,
            "score_breakdown": {}, "positive_evidence": [], "caution_evidence": [],
            "risk_flags": [],
        }
        episodes = [
            RecalledEpisode(stock_id=9, signal_date=date(2026, 1, 1), run_key="AGGREGATED",
                            direction="positive", score=0.3, sources={},
                            outcome={"label": "false_positive"}, distance=0.2),
        ]
        judged = judge_with_memory(aggregate, build_memory_reference(episodes))
        self.assertTrue(judged["needs_review"])       # raised
        self.assertEqual(judged["final_score"], 70.0)  # number still untouched


class AggregatorWithMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_memory_wired_does_not_change_headline_and_writes_episode(self):
        embedder = FakeEmbedder()
        repo = FakeEpisodeRepository()
        connection = FakeConnection(
            rows=[dart_agent_row(direction="positive", source_score=0.4, method_score=70.0)]
        )
        handler = AggregateSignalTaskHandler(
            connection,
            episode_writer=EpisodeWriter(embedder=embedder, repository=repo),
            episode_recall=EpisodeRecall(embedder=embedder, repository=repo),
        )
        result = await handler(
            {
                "id": 30, "stock_id": 1, "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )
        # headline unchanged by memory (SRC absent → neutral 50).
        self.assertEqual(result["signal"], "neutral")
        self.assertEqual(result["final_score"], 50.0)
        # episode written for the published signal.
        self.assertEqual(len(repo.upserts), 1)
        self.assertEqual(repo.upserts[0]["run_key"], "AGGREGATED")

    async def test_embedding_failure_still_publishes(self):
        embedder = FakeEmbedder(fail=True)
        repo = FakeEpisodeRepository()
        connection = FakeConnection(
            rows=[dart_agent_row(direction="positive", source_score=0.4, method_score=70.0)]
        )
        handler = AggregateSignalTaskHandler(
            connection,
            episode_writer=EpisodeWriter(embedder=embedder, repository=repo),
            episode_recall=EpisodeRecall(embedder=embedder, repository=repo),
        )
        result = await handler(
            {
                "id": 30, "stock_id": 1, "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )
        # publish path untouched despite embedding down.
        self.assertTrue(result["is_published"])
        self.assertIsNotNone(result["final_signal_id"])
        self.assertEqual(len(repo.upserts), 0)  # write degraded, no episode stored


if __name__ == "__main__":
    unittest.main()
