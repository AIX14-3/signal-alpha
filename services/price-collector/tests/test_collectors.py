import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.collectors.investor_flow import fetch_investor_flows, pick_flow_for_date
from app.collectors.stock_snapshot import SnapshotUnavailableError, fetch_stock_snapshot
from app.core.market_hours import KST
from tests.fakes import FakeRestClient, ka10001_payload, ka10059_payload

CAPTURED_AT = datetime(2026, 6, 11, 10, 30, tzinfo=KST)


def test_fetch_stock_snapshot_maps_spec_fields():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload()

    snapshot = asyncio.run(fetch_stock_snapshot(client, "005930", CAPTURED_AT))

    assert snapshot.ticker == "005930"
    assert snapshot.trade_date == date(2026, 6, 11)
    assert snapshot.current_price == Decimal("74300")
    assert snapshot.open == Decimal("73800")
    assert snapshot.high == Decimal("74500")
    assert snapshot.low == Decimal("73600")
    assert snapshot.volume == 12345678
    assert snapshot.trade_value == 915000
    assert snapshot.market_cap == 4435000
    assert snapshot.shares_outstanding == 5969783
    assert snapshot.per == Decimal("13.45")
    assert snapshot.roe == Decimal("9.81")
    assert snapshot.has_full_ohlc()


def test_fetch_stock_snapshot_partial_ohlc_detected():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload(open_pric="", high_pric="")

    snapshot = asyncio.run(fetch_stock_snapshot(client, "005930", CAPTURED_AT))

    assert snapshot.current_price == Decimal("74300")
    assert snapshot.open is None
    assert not snapshot.has_full_ohlc()


def test_fetch_stock_snapshot_requires_current_price():
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload(cur_prc="")

    with pytest.raises(SnapshotUnavailableError):
        asyncio.run(fetch_stock_snapshot(client, "005930", CAPTURED_AT))


def test_fetch_investor_flows_parses_rows_and_signs():
    client = FakeRestClient()
    client.payloads[("ka10059", "005930")] = ka10059_payload()

    flows = asyncio.run(fetch_investor_flows(client, "005930", date(2026, 6, 11)))

    assert len(flows) == 2
    today = pick_flow_for_date(flows, date(2026, 6, 11))
    assert today is not None
    assert today.individual_net == -120000
    assert today.foreign_net == 95000
    assert today.institution_net == 25000


def test_pick_flow_for_missing_date_returns_none():
    client = FakeRestClient()
    client.payloads[("ka10059", "005930")] = ka10059_payload()

    flows = asyncio.run(fetch_investor_flows(client, "005930", date(2026, 6, 11)))

    assert pick_flow_for_date(flows, date(2026, 6, 9)) is None
