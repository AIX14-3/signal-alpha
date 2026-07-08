"""체결 동기화 오케스트레이션 — 증분·멱등·미매핑·격리 (fake 연결/클라이언트)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.collectors.broker.base import NormalizedFill
from app.publish.trade_fills import sync_credential_fills, sync_due_credentials

_NOW = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)


def _fill(fid, ticker="005930", side="buy"):
    return NormalizedFill(
        broker_fill_id=fid, ticker=ticker, side=side,
        filled_at=datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc),
        quantity=Decimal("10"), price=Decimal("70000"), fee=None,
    )


class _FakeClient:
    def __init__(self, fills):
        self._fills = fills
        self.since_seen = "unset"

    async def fetch_fills(self, *, app_key, app_secret, account_ref, is_mock, since):
        self.since_seen = since
        return list(self._fills)


class _FakeConn:
    """user_trade_fills + resolve_stock_id 대역. 자연키로 멱등 흉내."""

    def __init__(self, *, known_tickers=("005930",), last_filled=None):
        self._known = set(known_tickers)
        self._last_filled = last_filled
        self._stored = set()  # (user, broker, fill_id)
        self.upserts = 0

    async def fetchval(self, sql, *args):
        if "max(filled_at)" in sql:
            return self._last_filled
        if "FROM stocks WHERE ticker" in sql:
            return 1 if args[0] in self._known else None
        return None

    async def execute(self, sql, *args):
        if "INSERT INTO user_trade_fills" in sql:
            key = (args[0], args[1], args[3])  # user, broker, broker_fill_id
            self.upserts += 1
            if key in self._stored:
                return "INSERT 0 0"  # ON CONFLICT DO NOTHING
            self._stored.add(key)
            return "INSERT 0 1"
        return "UPDATE 1"


_CRED = {
    "id": 1, "user_id": 7, "broker": "kiwoom", "account_ref": "ACC1",
    "is_mock": True, "app_key": "K", "app_secret": "S",
}


def test_sync_inserts_and_passes_incremental_cursor():
    conn = _FakeConn(last_filled=_NOW)
    client = _FakeClient([_fill("C1"), _fill("C2")])
    stats = asyncio.run(sync_credential_fills(conn, credential=_CRED, client=client, now=_NOW))
    assert stats.fetched == 2 and stats.inserted == 2 and stats.skipped == 0
    assert client.since_seen == _NOW  # last_filled_at 을 브로커 조회 커서로 넘김


def test_sync_is_idempotent_on_rerun():
    conn = _FakeConn()
    fills = [_fill("C1"), _fill("C2")]
    asyncio.run(sync_credential_fills(conn, credential=_CRED, client=_FakeClient(fills), now=_NOW))
    stats = asyncio.run(
        sync_credential_fills(conn, credential=_CRED, client=_FakeClient(fills), now=_NOW)
    )
    assert stats.inserted == 0 and stats.skipped == 2  # 재동기화 중복 0


def test_sync_counts_unmapped_ticker_but_still_stores():
    conn = _FakeConn(known_tickers=("005930",))
    client = _FakeClient([_fill("C1", ticker="999999")])  # 미상장
    stats = asyncio.run(sync_credential_fills(conn, credential=_CRED, client=client, now=_NOW))
    assert stats.inserted == 1 and stats.unmapped == 1


class _StoreConn:
    """sync_due_credentials 용 — 자격증명 목록/복호/상태 대역."""

    def __init__(self, targets, secrets):
        self._targets = targets
        self._secrets = secrets
        self.marked_synced = []
        self.marked_error = []

    async def fetch(self, sql, *args):
        if "FROM user_broker_credentials" in sql and "status = 'active'" in sql:
            return self._targets
        return []

    async def fetchrow(self, sql, *args):
        if "app_key_enc" in sql:  # get_secret_for_sync
            return None  # 사용 안 함(아래 monkeypatch 대신 secrets 주입)
        return None

    async def fetchval(self, sql, *args):
        if "max(filled_at)" in sql:
            return None
        if "FROM stocks WHERE ticker" in sql:
            return 1
        return None

    async def execute(self, sql, *args):
        if "SET last_synced_at" in sql:
            self.marked_synced.append(args[0])
        elif "SET status = $2" in sql:
            self.marked_error.append((args[0], args[1]))
        elif "INSERT INTO user_trade_fills" in sql:
            return "INSERT 0 1"
        return "UPDATE 1"


def test_sync_due_isolates_failing_credential(monkeypatch):
    # 자격증명 2건: id=1 정상, id=2 는 클라이언트가 폭발 → 격리, 1은 성공.
    targets = [
        {"id": 1, "user_id": 7, "broker": "toss", "account_ref": "A"},
        {"id": 2, "user_id": 8, "broker": "kiwoom", "account_ref": "B"},
    ]
    secrets = {
        1: {"id": 1, "user_id": 7, "broker": "toss", "account_ref": "A", "is_mock": False,
            "app_key": "k", "app_secret": "s"},
        2: {"id": 2, "user_id": 8, "broker": "kiwoom", "account_ref": "B", "is_mock": False,
            "app_key": "k", "app_secret": "s"},
    }
    conn = _StoreConn(targets, secrets)

    # get_secret_for_sync 를 secrets 주입으로 대체.
    import app.publish.trade_fills as mod

    async def fake_secret(self, *, credential_id):
        return secrets.get(credential_id)

    monkeypatch.setattr(mod.UserBrokerCredentialRepository, "get_secret_for_sync", fake_secret)

    class _Client:
        def __init__(self, broker):
            self._broker = broker

        async def fetch_fills(self, **kw):
            if self._broker == "kiwoom":
                raise RuntimeError("broker down")
            return [_fill("C1", ticker="005930")]

    summary = asyncio.run(
        sync_due_credentials(conn, stale_before=_NOW, now=_NOW, client_factory=_Client)
    )
    assert summary["credentials"] == 1 and summary["failed_credentials"] == 1
    assert conn.marked_synced == [1]
    assert conn.marked_error and conn.marked_error[0][0] == 2  # id=2 error 표기
