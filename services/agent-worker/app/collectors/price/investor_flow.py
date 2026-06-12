"""ka10059 종목별투자자기관별 — daily confirmed investor net-buy collector.

Run once after market close; intraday investor figures are not final.
"""

from __future__ import annotations

from datetime import date, datetime

from app.collectors.price.kiwoom.parsing import parse_signed_int
from app.collectors.price.kiwoom.rest_client import KiwoomRestClient
from app.collectors.price.schemas import InvestorFlow

API_ID = "ka10059"

ROWS_KEY = "stk_invsr_orgn"  # response list of per-date rows
KA10059_FIELDS = {
    "trade_date": "dt",  # YYYYMMDD
    "individual_net": "ind_invsr",  # 개인투자자 순매수 (주)
    "foreign_net": "frgnr_invsr",  # 외국인투자자 순매수 (주)
    "institution_net": "orgn",  # 기관계 순매수 (주)
}


async def fetch_investor_flows(
    client: KiwoomRestClient, ticker: str, base_date: date
) -> list[InvestorFlow]:
    payload = await client.request(
        API_ID,
        {
            "dt": base_date.strftime("%Y%m%d"),
            "stk_cd": ticker,
            "amt_qty_tp": "1",  # 1: 수량
            "trde_tp": "0",  # 0: 순매수
            "unit_tp": "1",
        },
    )

    flows: list[InvestorFlow] = []
    for row in payload.get(ROWS_KEY) or []:
        raw_date = str(row.get(KA10059_FIELDS["trade_date"], "")).strip()
        if len(raw_date) != 8:
            continue
        flows.append(
            InvestorFlow(
                ticker=ticker,
                trade_date=datetime.strptime(raw_date, "%Y%m%d").date(),
                individual_net=parse_signed_int(row.get(KA10059_FIELDS["individual_net"])),
                foreign_net=parse_signed_int(row.get(KA10059_FIELDS["foreign_net"])),
                institution_net=parse_signed_int(row.get(KA10059_FIELDS["institution_net"])),
            )
        )
    return flows


def pick_flow_for_date(flows: list[InvestorFlow], trade_date: date) -> InvestorFlow | None:
    for flow in flows:
        if flow.trade_date == trade_date:
            return flow
    return None
