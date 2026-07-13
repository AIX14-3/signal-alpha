"""SCORE_COHORT 프로듀서 — 코호트 청크의 결정론·sector soft-정렬 계약."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.cohort.producer import chunk_universe


def test_chunks_are_deterministic_and_fixed_size():
    rows = [("005930", "반도체"), ("000660", "반도체"), ("035420", "인터넷"), ("035720", "인터넷"), ("259960", "게임")]
    first = chunk_universe(rows, 2)
    second = chunk_universe(list(reversed(rows)), 2)
    assert first == second  # 입력 순서 무관 — dedupe 가 같은 task_context 를 잡는 조건
    assert all(len(c) <= 2 for c in first)
    assert sorted(t for c in first for t in c) == sorted(t for t, _ in rows)


def test_sector_is_soft_sort_key_nulls_last():
    rows = [("111111", None), ("005930", "반도체"), ("000660", "반도체")]
    chunks = chunk_universe(rows, 2)
    # 같은 섹터(반도체)가 같은 청크에 인접하고, sector 없는 종목은 뒤로 밀린다.
    assert chunks[0] == ["000660", "005930"]
    assert chunks[1] == ["111111"]


def test_cohort_size_floor_is_one():
    assert chunk_universe([("005930", None)], 0) == [["005930"]]
