"""SCORE_COHORT 핸들러 — LLM 성공/실패 폴백(rules·no_signal)과 발행 계약."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

import app.orchestrator.cohort.tasks as cohort_tasks
from app.orchestrator.cohort.tasks import CohortScoreTaskHandler


class _FakeConnection:
    def __init__(self, stock_rows, prev_score_rows=None):
        self._stock_rows = stock_rows
        self._prev_score_rows = prev_score_rows or []

    async def fetch(self, query, *args):
        if "agent_results" in query:
            return self._prev_score_rows
        return self._stock_rows

    async def fetchval(self, query, *args):
        return 70000.0


class _FakeLoader:
    def __init__(self, rows):
        self._rows = rows

    async def load(self, *, stock_id, stock_code, as_of):
        return [SimpleNamespace(metadata={"postings": list(self._rows)})]


class _FakeSpec:
    source = "HIRING"
    run_key = "HIRING"
    debate_method = "D-1"
    date_key = "observed_date"
    metadata_key = "postings"
    needs_close = False

    def __init__(self, rows):
        self._rows = rows

    def build_loader(self, *, repository, connection):
        return _FakeLoader(self._rows)


class _RecordingPersistence:
    saved = []

    def __init__(self, connection, *, registrations=None, runtime_config=None):
        pass

    async def save(self, *, stock_id, signal, analysis_date, publish_final_signal, run_key):
        _RecordingPersistence.saved.append(
            {"stock_id": stock_id, "signal": signal, "run_key": run_key}
        )
        return {"analysis_result_id": 11, "agent_result_ids": [21], "final_signal_id": 31}


class _FakeClient:
    model = "fake-model"

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error
        self.prompts = []

    async def generate_json(self, prompt, schema=None):
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        return self._payload


async def _fake_enqueue_aggregate(queue, *, stock_id, aggregate_ctx, priority="batch"):
    return 99


def _rows():
    return [
        {"observed_date": "2026-07-01", "job_title": "반도체 공정 엔지니어"},
        {"observed_date": "2026-07-10", "job_title": "AI 연구원"},
    ]


def _task():
    return {
        "stock_id": None,
        "task_context": {"source": "HIRING", "as_of": "2026-07-13", "tickers": ["005930"]},
    }


def _settings(fallback="rules"):
    return SimpleNamespace(
        llm_scoring_fallback=fallback,
        llm_scoring_provider="vertex",
        llm_scoring_model="fake",
    )


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    _RecordingPersistence.saved = []
    monkeypatch.setitem(cohort_tasks.COHORT_SOURCES, "HIRING", _FakeSpec(_rows()))
    monkeypatch.setattr(cohort_tasks, "AlternativeSignalPersistence", _RecordingPersistence)
    monkeypatch.setattr(cohort_tasks, "enqueue_aggregate", _fake_enqueue_aggregate)


def _stock_rows():
    return [{"id": 1, "ticker": "005930", "name": "삼성전자"}]


def test_llm_success_publishes_with_llm_provenance():
    handler = CohortScoreTaskHandler(
        _FakeConnection(_stock_rows()),
        settings=_settings(),
        client_factory=lambda: _FakeClient(
            payload={"scores": [{
                "ticker": "005930", "score": 0.4, "confidence": 0.6,
                "no_signal": False, "evidence": ["채용 확대 흔적"],
            }]}
        ),
    )
    out = asyncio.run(handler(_task()))
    assert out["status"] == "success"
    assert out["mode"] == "llm"
    assert out["published_count"] == 1
    saved = _RecordingPersistence.saved[0]
    assert saved["run_key"] == "HIRING"
    result = saved["signal"].per_source["HIRING"]
    assert result.analysis_source == "llm"
    assert result.score == 0.4
    assert result.llm_model == "fake-model"


def test_llm_failure_with_no_signal_fallback_writes_nothing():
    handler = CohortScoreTaskHandler(
        _FakeConnection(_stock_rows()),
        settings=_settings(fallback="no_signal"),
        client_factory=lambda: _FakeClient(error=RuntimeError("429")),
    )
    out = asyncio.run(handler(_task()))
    assert out["status"] == "llm_failed_no_write"
    assert _RecordingPersistence.saved == []  # 어제 점수가 last-known 재사용으로 승계된다


def test_llm_failure_with_rules_fallback_publishes_observably(monkeypatch):
    import app.backtest.reference_scorer as reference_scorer

    async def _fake_score_source(kind, pit, asof, cfg, sector, ind_fn, eval_fn, *, current_close=None):
        return 0.25

    monkeypatch.setattr(reference_scorer, "score_source", _fake_score_source)
    handler = CohortScoreTaskHandler(
        _FakeConnection(_stock_rows()),
        settings=_settings(fallback="rules"),
        client_factory=lambda: _FakeClient(error=RuntimeError("schema violation")),
    )
    out = asyncio.run(handler(_task()))
    assert out["status"] == "success"
    assert out["mode"] == "rules_fallback"
    result = _RecordingPersistence.saved[0]["signal"].per_source["HIRING"]
    assert result.analysis_source == "rules_fallback"
    assert result.score == 0.25
    assert "schema violation" in (result.llm_error or "")


def test_prev_score_recalled_pit_and_injected_into_prompt():
    """메모리를 채점 입력으로: 직전 점수(PIT)가 prev_score 로 프롬프트에 주입돼
    앵커링 규범(|Δ|>0.3 → score_change_reason 필수)이 발화한다."""
    client = _FakeClient(
        payload={"scores": [{
            "ticker": "005930", "score": 0.1, "confidence": 0.4,
            "no_signal": False, "evidence": ["소폭 개선"],
        }]}
    )
    handler = CohortScoreTaskHandler(
        _FakeConnection(
            _stock_rows(),
            prev_score_rows=[
                {"analysis_date": "2026-07-12", "score": "-0.2"},
                {"analysis_date": "2026-07-11", "score": "0.1"},
            ],
        ),
        settings=_settings(),
        client_factory=lambda: client,
    )
    out = asyncio.run(handler(_task()))
    assert out["status"] == "success"
    prompt = client.prompts[0]
    assert '"prev_score": -0.2' in prompt  # 가장 최근(직전) 점수만 앵커
    assert "own_scores_recent" in prompt  # 최근 궤적은 self_history 로


def test_pit_filter_drops_future_rows():
    rows = _rows() + [{"observed_date": "2026-08-01", "job_title": "미래 공고(누수)"}]
    handler = CohortScoreTaskHandler(
        _FakeConnection(_stock_rows()),
        settings=_settings(),
        client_factory=lambda: _FakeClient(
            payload={"scores": [{
                "ticker": "005930", "score": 0.0, "confidence": 0.2,
                "no_signal": True, "evidence": [],
            }]}
        ),
    )
    # 코호트 컨텍스트에 미래 행이 들어가면 안 된다 — build_evidence 입력을 가로채 확인.
    captured = {}
    original = cohort_tasks.build_evidence

    def _spy(source, pit, close):
        captured["pit"] = pit
        return original(source, pit, close)

    cohort_tasks.COHORT_SOURCES["HIRING"] = _FakeSpec(rows)
    try:
        cohort_tasks.build_evidence = _spy
        asyncio.run(handler(_task()))
    finally:
        cohort_tasks.build_evidence = original
    assert all(r["observed_date"] <= "2026-07-13" for r in captured["pit"])
