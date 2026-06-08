"""Kiwoom TR codes, output block names, and MVP universe metadata.

Field labels and codes follow ``docs`` / ``kiwoom_data_spec`` for the C-4
Price Collector.
"""

from dataclasses import dataclass

# --- TR codes -------------------------------------------------------------
TR_STOCK_BASIC = "opt10001"     # 주식 기본 정보
TR_DAILY_CHART = "opt10081"     # 주식 일봉 차트
TR_WEEKLY_CHART = "opt10082"    # 주식 주봉 차트
TR_MONTHLY_CHART = "opt10083"   # 주식 월봉 차트
TR_YEARLY_CHART = "opt10084"    # 주식 년봉 차트
TR_SECTOR_QUOTE = "opt20004"    # 업종별 현재 시세
TR_SECTOR_DAILY = "opt20006"    # 업종 일봉 차트
TR_INVESTOR_FLOW = "opt10059"   # 종목별 투자자 매매동향

# --- block_request output names ------------------------------------------
OUTPUT_STOCK_BASIC = "주식기본정보"
OUTPUT_DAILY_CHART = "주식일봉차트조회"
OUTPUT_INVESTOR_FLOW = "종목별투자자기관별"
OUTPUT_SECTOR_QUOTE = "업종별시세"
OUTPUT_SECTOR_DAILY = "업종일봉차트조회"

# --- Kiwoom market codes (시장구분) --------------------------------------
MARKET_KOSPI = "0"
MARKET_KOSDAQ = "1"


@dataclass(frozen=True)
class StockMeta:
    """Static metadata for an MVP universe stock."""

    ticker: str
    name: str
    sector_code: str
    sector_name: str
    market_code: str = MARKET_KOSPI


# MVP universe with Kiwoom sector codes (see spec section 2).
MVP_STOCKS: tuple[StockMeta, ...] = (
    StockMeta("005930", "삼성전자", "004", "전기전자"),
    StockMeta("000660", "SK하이닉스", "004", "전기전자"),
    StockMeta("035420", "네이버", "047", "서비스업"),
    StockMeta("005380", "현대차", "003", "운수장비"),
    StockMeta("105560", "KB금융", "022", "금융업"),
    StockMeta("005490", "POSCO홀딩스", "006", "철강금속")
)

MVP_TICKERS: tuple[str, ...] = tuple(stock.ticker for stock in MVP_STOCKS)
