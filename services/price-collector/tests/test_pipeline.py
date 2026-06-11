import asyncio
from datetime import date, datetime

from app.core.market_hours import KST
from app.pipeline import run_investor_flow_update, run_snapshot_cycle
from app.schemas.snapshot import TargetStock
from tests.fakes import FakeRepository, FakeRestClient, ka10001_payload, ka10059_payload

CAPTURED_AT = datetime(2026, 6, 11, 10, 30, tzinfo=KST)
TARGETS = [
    TargetStock(stock_id=1, ticker="005930", name="삼성전자"),
    TargetStock(stock_id=2, ticker="000660", name="SK하이닉스"),
]


def test_snapshot_cycle_stores_all_targets():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload()
    client.payloads[("ka10001", "000660")] = ka10001_payload(stk_cd="000660", cur_prc="+201000")
    repo = FakeRepository(TARGETS)

    stats = asyncio.run(run_snapshot_cycle(client, repo, TARGETS, CAPTURED_AT))

    assert stats.collected == 2
    assert stats.stored == 2
    assert stats.failed == 0
    assert len(repo.snapshots) == 2
    assert len(repo.ohlcv_upserts) == 2


def test_snapshot_cycle_one_failure_does_not_stop_sweep():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload()
    client.errors[("ka10001", "000660")] = RuntimeError("rate limited")
    repo = FakeRepository(TARGETS)

    stats = asyncio.run(run_snapshot_cycle(client, repo, TARGETS, CAPTURED_AT))

    assert stats.stored == 1
    assert stats.failed == 1
    assert "000660" in stats.errors[0]
    assert len(repo.snapshots) == 1


def test_snapshot_cycle_partial_ohlc_counts_skip():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload(open_pric="")
    targets = TARGETS[:1]
    repo = FakeRepository(targets)

    stats = asyncio.run(run_snapshot_cycle(client, repo, targets, CAPTURED_AT))

    assert stats.stored == 1  # snapshot row still stored
    assert stats.skipped == 1  # ohlcv upsert skipped
    assert len(repo.ohlcv_upserts) == 0


def test_investor_flow_update_attaches_to_existing_rows():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload()
    client.payloads[("ka10059", "005930")] = ka10059_payload()
    targets = TARGETS[:1]
    repo = FakeRepository(targets)

    # First create today's ohlcv row via a snapshot cycle, then attach flows.
    asyncio.run(run_snapshot_cycle(client, repo, targets, CAPTURED_AT))
    stats = asyncio.run(run_investor_flow_update(client, repo, targets, date(2026, 6, 11)))

    assert stats.stored == 1
    assert repo.flow_updates[0][1].foreign_net == 95000


def test_investor_flow_update_skips_when_no_ohlcv_row():
    client = FakeRestClient()
    client.payloads[("ka10059", "005930")] = ka10059_payload()
    targets = TARGETS[:1]
    repo = FakeRepository(targets)

    stats = asyncio.run(run_investor_flow_update(client, repo, targets, date(2026, 6, 11)))

    assert stats.stored == 0
    assert stats.skipped == 1


def test_investor_flow_update_skips_when_date_missing():
    client = FakeRestClient()
    client.payloads[("ka10059", "005930")] = ka10059_payload(rows=[])
    targets = TARGETS[:1]
    repo = FakeRepository(targets)

    stats = asyncio.run(run_investor_flow_update(client, repo, targets, date(2026, 6, 11)))

    assert stats.stored == 0
    assert stats.skipped == 1
