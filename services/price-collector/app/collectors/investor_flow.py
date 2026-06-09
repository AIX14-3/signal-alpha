from datetime import datetime

from app.core.constants import OUTPUT_INVESTOR_FLOW, TR_INVESTOR_FLOW
from app.kiwoom.client import KiwoomClient
from app.kiwoom.parsing import field, parse_date, parse_decimal, parse_int
from app.schemas.price import InvestorFlow


class InvestorFlowCollector:
    """OPT10059 · 종목별 투자자 매매동향 → list[InvestorFlow]."""

    tr_code = TR_INVESTOR_FLOW
    output = OUTPUT_INVESTOR_FLOW

    def __init__(self, client: KiwoomClient) -> None:
        self._client = client

    def collect(self, ticker: str, base_date: str | None = None) -> list[InvestorFlow]:
        base = base_date or datetime.now().strftime("%Y%m%d")
        records = self._client.request(
            self.tr_code,
            self.output,
            일자=base,
            종목코드=ticker,
            금액수량구분="1",  # 1: 수량
            매매구분="0",      # 0: 순매수
            단위구분="1",
            next="0"
        )
        flows = [self._to_flow(record) for record in records]
        return [flow for flow in flows if flow is not None]

    @staticmethod
    def _to_flow(record: dict[str, str]) -> InvestorFlow | None:
        trade_date = parse_date(field(record, "일자"))
        if trade_date is None:
            return None
        return InvestorFlow(
            trade_date=trade_date,
            individual_net=parse_int(field(record, "개인투자자", "개인")),
            foreign_net=parse_int(field(record, "외국인투자자", "외국인")),
            institution_net=parse_int(field(record, "기관계", "기관")),
            foreign_holding=parse_int(field(record, "외국인보유량")) or None,
            foreign_holding_pct=parse_decimal(field(record, "외국인보유율"))
        )
