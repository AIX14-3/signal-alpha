from app.core.constants import (
    MARKET_KOSPI,
    OUTPUT_SECTOR_QUOTE,
    TR_SECTOR_QUOTE
)
from app.kiwoom.client import KiwoomClient
from app.kiwoom.parsing import field, parse_decimal, parse_int, parse_points
from app.schemas.sector import SectorQuote


class SectorQuoteCollector:
    """OPT20004 · 업종별 현재 시세 → SectorQuote (point-in-time snapshot)."""

    tr_code = TR_SECTOR_QUOTE
    output = OUTPUT_SECTOR_QUOTE

    def __init__(self, client: KiwoomClient) -> None:
        self._client = client

    def collect(
        self,
        sector_code: str,
        market_code: str = MARKET_KOSPI
    ) -> SectorQuote | None:
        records = self._client.request(
            self.tr_code,
            self.output,
            시장구분=market_code,
            업종코드=sector_code,
            next="0"
        )
        if not records:
            return None
        return self._to_quote(records[0])

    @staticmethod
    def _to_quote(record: dict[str, str]) -> SectorQuote:
        return SectorQuote(
            name=field(record, "업종명") or None,
            close=parse_points(field(record, "현재가")),
            change=parse_decimal(field(record, "전일대비")),
            change_pct=parse_decimal(field(record, "등락률")),
            volume=parse_int(field(record, "거래량")) or None,
            trading_value=parse_int(field(record, "거래대금")) or None
        )
