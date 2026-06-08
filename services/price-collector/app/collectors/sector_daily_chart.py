from datetime import datetime

from app.core.constants import OUTPUT_SECTOR_DAILY, TR_SECTOR_DAILY
from app.kiwoom.client import KiwoomClient
from app.kiwoom.parsing import field, parse_date, parse_int, parse_points
from app.schemas.sector import SectorDailyCandle


class SectorDailyChartCollector:
    """OPT20006 · 업종 일봉 차트 → list[SectorDailyCandle]."""

    tr_code = TR_SECTOR_DAILY
    output = OUTPUT_SECTOR_DAILY

    def __init__(self, client: KiwoomClient) -> None:
        self._client = client

    def collect(
        self,
        sector_code: str,
        base_date: str | None = None
    ) -> list[SectorDailyCandle]:
        base = base_date or datetime.now().strftime("%Y%m%d")
        records = self._client.request(
            self.tr_code,
            self.output,
            업종코드=sector_code,
            기준일자=base,
            next="0"
        )
        candles = [self._to_candle(record) for record in records]
        return [candle for candle in candles if candle is not None]

    @staticmethod
    def _to_candle(record: dict[str, str]) -> SectorDailyCandle | None:
        trade_date = parse_date(field(record, "일자"))
        if trade_date is None:
            return None
        return SectorDailyCandle(
            trade_date=trade_date,
            open=parse_points(field(record, "시가")),
            high=parse_points(field(record, "고가")),
            low=parse_points(field(record, "저가")),
            close=parse_points(field(record, "현재가", "종가")),
            volume=parse_int(field(record, "거래량")) or None,
            trading_value=parse_int(field(record, "거래대금")) or None
        )
