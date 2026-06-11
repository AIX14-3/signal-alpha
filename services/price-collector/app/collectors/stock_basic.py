from app.core.constants import OUTPUT_STOCK_BASIC, TR_STOCK_BASIC
from app.kiwoom.client import KiwoomClient
from app.kiwoom.parsing import field, parse_decimal, parse_int, parse_price
from app.schemas.price import StockBasic


class StockBasicCollector:
    """OPT10001 · 주식 기본 정보 → StockBasic (single snapshot per stock)."""

    tr_code = TR_STOCK_BASIC
    output = OUTPUT_STOCK_BASIC

    def __init__(self, client: KiwoomClient) -> None:
        self._client = client

    def collect(self, ticker: str, base_date: str | None = None) -> StockBasic | None:
        records = self._client.request(
            self.tr_code,
            self.output,
            종목코드=ticker,
            next="0"
        )
        if not records:
            return None
        return self._to_basic(ticker, records[0])

    @staticmethod
    def _to_basic(ticker: str, record: dict[str, str]) -> StockBasic:
        return StockBasic(
            ticker=ticker,
            close=parse_price(field(record, "현재가")),
            market_cap=parse_int(field(record, "시가총액")) or None,
            listed_shares=parse_int(field(record, "상장주수", "상장주식수")) or None,
            per=parse_decimal(field(record, "PER")),
            pbr=parse_decimal(field(record, "PBR")),
            eps=parse_decimal(field(record, "EPS")),
            bps=parse_decimal(field(record, "BPS")),
            roe=parse_decimal(field(record, "ROE")),
            roa=parse_decimal(field(record, "ROA"))
        )
