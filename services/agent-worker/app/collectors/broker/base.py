"""증권사 계좌 체결내역 클라이언트 — 공통 모델 + 정규화 헬퍼.

브로커별(키움/토스) 응답 스키마가 다르므로, 각 클라이언트가 원본을 **공통 NormalizedFill**
로 정규화한다. 정규화 함수는 순수(입력 payload → NormalizedFill 목록)라 faked 응답으로
단위테스트한다. ⚠️ 실제 엔드포인트/필드 정합은 유저 실키(모의계좌 포함) 없이는 라이브
검증이 불가 — 여기 파싱은 문서 기반이며 방어적(누락 필드는 그 체결만 skip)이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


@dataclass(frozen=True)
class NormalizedFill:
    """공통 체결 단위 — user_trade_fills 컬럼과 1:1."""

    broker_fill_id: str
    ticker: str
    side: str  # 'buy' | 'sell'
    filled_at: datetime
    quantity: Decimal
    price: Decimal
    fee: Decimal | None = None


class BrokerAccountClient(Protocol):
    """유저 자격증명으로 계좌 체결내역을 조회해 NormalizedFill 목록을 돌려준다."""

    async def fetch_fills(
        self,
        *,
        app_key: str,
        app_secret: str,
        account_ref: str,
        is_mock: bool,
        since: datetime | None,
    ) -> list[NormalizedFill]: ...


def to_decimal(value: Any) -> Decimal | None:
    """문자열/숫자 → Decimal. 파싱 불가면 None(방어적)."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def normalize_side(raw: Any, *, buy_tokens: set[str], sell_tokens: set[str]) -> str | None:
    """브로커별 매매구분 토큰 → 'buy'/'sell'. 미상이면 None(skip)."""
    token = str(raw).strip().lower()
    if token in buy_tokens:
        return "buy"
    if token in sell_tokens:
        return "sell"
    return None
