"""run_collectors.run_once: 타깃별 장애 격리.

한 종목의 수집 API 에러(KIPRIS 쿼터 소진 등)가 나머지 타깃 수집을 통째로
중단시키지 않아야 한다. 또한 한 소스의 *모든* 타깃이 터지면 비-제로 종료해
CI 에서 빨갛게 보여야 한다.
"""
from __future__ import annotations

import argparse
import asyncio

import pytest

import run_collectors
from app.clients.kipris_client import KiprisApiError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def acquire(self):
        return _FakeAcquire()

    async def close(self):
        return None


async def _fake_create_pool(**_kwargs):
    return FakePool()


class FakePatentCollector:
    """Records run() calls; raises a KIPRIS-style error for tickers in ``fail``."""

    instances: list["FakePatentCollector"] = []
    fail: set[str] = set()

    def __init__(self, *, pool, client, collector_ver=None):
        self.calls: list[str] = []
        FakePatentCollector.instances.append(self)

    async def run(self, *, stock_id, stock_code, stock_name=None,
                  applicant_names=None, start_date=None, end_date=None):
        self.calls.append(stock_code)
        if stock_code in FakePatentCollector.fail:
            raise KiprisApiError(
                "KIPRIS API error 22: LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
            )
        return {"status": "success", "inserted_count": 1, "skipped_count": 0}


TARGETS = [
    {"stock_id": 1, "ticker": "005930", "stock_name": "삼성전자", "applicant_names": []},
    {"stock_id": 2, "ticker": "000660", "stock_name": "SK하이닉스", "applicant_names": []},
]


def _patent_args() -> argparse.Namespace:
    return argparse.Namespace(
        ticker=None, start_date=None, end_date=None,
        patent_only=True, datalab_only=False, loop=False, interval_seconds=3600,
    )


def _patch(monkeypatch, *, fail: set[str]) -> None:
    FakePatentCollector.instances = []
    FakePatentCollector.fail = fail
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("KIPRIS_API_KEY", "dummy-key")  # FakePatentCollector ignores it
    monkeypatch.setattr(run_collectors.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(run_collectors, "PatentCollector", FakePatentCollector)

    async def _targets(_conn, _ticker):
        return TARGETS

    monkeypatch.setattr(run_collectors, "fetch_patent_targets", _targets)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_failing_target_does_not_abort_remaining(monkeypatch):
    """첫 타깃이 KIPRIS 에러를 던져도 둘째 타깃 run() 이 호출되고, 부분 실패는
    예외 없이(0 종료) 끝난다."""
    _patch(monkeypatch, fail={"005930"})

    asyncio.run(run_collectors.run_once(_patent_args()))

    calls = FakePatentCollector.instances[0].calls
    assert calls == ["005930", "000660"], "두 타깃 모두 시도되어야 한다(격리)"


def test_all_targets_failing_exits_nonzero(monkeypatch):
    """모든 타깃이 터지면 SystemExit 으로 종료(소스 전체 실패 신호). 단, 그 전에
    모든 타깃을 시도는 한다."""
    _patch(monkeypatch, fail={"005930", "000660"})

    with pytest.raises(SystemExit):
        asyncio.run(run_collectors.run_once(_patent_args()))

    calls = FakePatentCollector.instances[0].calls
    assert calls == ["005930", "000660"], "전체 실패여도 모든 타깃을 시도해야 한다"
