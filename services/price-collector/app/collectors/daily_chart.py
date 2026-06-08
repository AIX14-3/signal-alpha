from datetime import datetime

from app.core.constants import OUTPUT_DAILY_CHART, TR_DAILY_CHART
from app.kiwoom.client import KiwoomClient
from app.kiwoom.parsing import field, parse_date, parse_int, parse_price
from app.schemas.price import DailyCandle


class DailyChartCollector:
    """OPT10081 · 주식 일봉 차트 → list[DailyCandle]."""

    tr_code = TR_DAILY_CHART
    output = OUTPUT_DAILY_CHART

    def __init__(self, client: KiwoomClient, use_adjusted_price: bool = True) -> None:
        self._client = client
        self._adjusted = use_adjusted_price

    def collect(self, ticker: str, base_date: str | None = None) -> list[DailyCandle]:
        base = base_date or datetime.now().strftime("%Y%m%d")
        records = self._client.request(
            self.tr_code,
            self.output,
            종목코드=ticker,
            기준일자=base,
            수정주가구분="1" if self._adjusted else "0",
            next="0"
        )
        candles = [self._to_candle(record) for record in records]
        return [candle for candle in candles if candle is not None]

    @staticmethod
    def _to_candle(record: dict[str, str]) -> DailyCandle | None:
        trade_date = parse_date(field(record, "일자"))
        if trade_date is None:
            return None
        return DailyCandle(
            trade_date=trade_date,
            open=parse_price(field(record, "시가")),
            high=parse_price(field(record, "고가")),
            low=parse_price(field(record, "저가")),
            close=parse_price(field(record, "현재가", "종가")),
            volume=parse_int(field(record, "거래량")),
            trading_value=parse_int(field(record, "거래대금")) or None
        )
