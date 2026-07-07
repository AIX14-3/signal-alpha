"""유저 체결내역 동기화 로직 — 브로커 API → user_trade_fills(백엔드 DB).

자격증명별로 브로커 계좌에서 증분(마지막 체결 이후) 체결을 조회해 ticker→stock_id 매핑 후
(user_id, broker, broker_fill_id) 자연키로 멱등 upsert 한다. 연결·리포지토리·브로커
클라이언트를 모두 주입받는 순수 오케스트레이션이라 faked 로 단위테스트한다(HTTP 없이).

credentials/fills 모두 백엔드 DB(자격증명·저널·stocks 공존)라 source 풀이 없다 — 연결 하나.
자격증명 1건 실패가 배치 전체를 막지 않는다(journal_outcomes 격리 철학, NFR-5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.collectors.broker.base import BrokerAccountClient
from app.collectors.broker.kiwoom_account import KiwoomAccountClient
from app.collectors.broker.toss_account import TossAccountClient
from signal_alpha_data_access.repositories.user_broker_credentials import (
    UserBrokerCredentialRepository,
)
from signal_alpha_data_access.repositories.user_trade_fills import UserTradeFillsRepository

logger = logging.getLogger(__name__)


@dataclass
class FillSyncStats:
    fetched: int = 0  # 브로커가 돌려준 체결 수
    inserted: int = 0  # 새로 적재
    skipped: int = 0  # 이미 있음(멱등)
    unmapped: int = 0  # ticker→stock_id 매핑 실패(체결은 stock_id NULL 로 적재)
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def build_client(broker: str) -> BrokerAccountClient:
    """브로커별 계좌 클라이언트 팩토리."""
    if broker == "toss":
        return TossAccountClient()
    if broker == "kiwoom":
        return KiwoomAccountClient()
    raise ValueError(f"지원하지 않는 브로커: {broker}")


async def sync_credential_fills(
    conn: Any,
    *,
    credential: dict[str, Any],
    client: BrokerAccountClient,
    now: datetime,
) -> FillSyncStats:
    """복호된 자격증명 1건에 대해 증분 체결을 조회·적재한다.

    credential = {id, user_id, broker, account_ref, is_mock, app_key, app_secret}
    (UserBrokerCredentialRepository.get_secret_for_sync 반환 형태).
    """
    fills_repo = UserTradeFillsRepository(conn)
    stats = FillSyncStats()

    since = await fills_repo.last_filled_at(
        user_id=credential["user_id"], broker=credential["broker"]
    )
    fills = await client.fetch_fills(
        app_key=credential["app_key"],
        app_secret=credential["app_secret"],
        account_ref=credential["account_ref"],
        is_mock=credential["is_mock"],
        since=since,
    )
    stats.fetched = len(fills)

    for fill in fills:
        try:
            stock_id = await fills_repo.resolve_stock_id(ticker=fill.ticker)
            if stock_id is None:
                stats.unmapped += 1
            inserted = await fills_repo.upsert_fill(
                user_id=credential["user_id"],
                broker=credential["broker"],
                account_ref=credential["account_ref"],
                broker_fill_id=fill.broker_fill_id,
                stock_id=stock_id,
                ticker=fill.ticker,
                side=fill.side,
                filled_at=fill.filled_at,
                quantity=fill.quantity,
                price=fill.price,
                fee=fill.fee,
            )
            if inserted:
                stats.inserted += 1
            else:
                stats.skipped += 1
        except Exception as exc:  # noqa: BLE001 - 체결 1건 실패 격리
            stats.failed += 1
            stats.errors.append(f"fill={fill.broker_fill_id}: {exc}")
            logger.warning("fill upsert failed (%s): %s", fill.broker_fill_id, exc)

    return stats


async def sync_due_credentials(
    conn: Any,
    *,
    stale_before: datetime,
    now: datetime,
    client_factory: Any = build_client,
) -> dict[str, Any]:
    """요청됐거나 오래된 활성 자격증명을 순회 동기화(배치 러너 진입점).

    자격증명 1건 실패가 전체를 막지 않는다. 성공 시 mark_synced(요청 플래그도 소거),
    실패 시 mark_status('error').
    """
    cred_repo = UserBrokerCredentialRepository(conn)
    targets = await cred_repo.list_sync_targets(stale_before=stale_before)
    summary = {"credentials": 0, "inserted": 0, "failed_credentials": 0, "errors": []}

    for target in targets:
        credential_id = int(target["id"])
        try:
            secret = await cred_repo.get_secret_for_sync(credential_id=credential_id)
            if secret is None:  # revoked 사이 삭제 등
                continue
            client = client_factory(secret["broker"])
            stats = await sync_credential_fills(conn, credential=secret, client=client, now=now)
            await cred_repo.mark_synced(credential_id=credential_id)
            summary["credentials"] += 1
            summary["inserted"] += stats.inserted
            if stats.errors:
                summary["errors"].extend(stats.errors[:3])
        except Exception as exc:  # noqa: BLE001 - 자격증명 1건 실패 격리
            summary["failed_credentials"] += 1
            summary["errors"].append(f"credential={credential_id}: {exc}")
            await cred_repo.mark_status(
                credential_id=credential_id, status="error", last_error=str(exc)[:500]
            )
            logger.warning("credential sync failed (id=%s): %s", credential_id, exc)

    return summary
