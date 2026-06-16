"""OpenDART 표준계정 → 정규 metric 매핑 (L1 정형 재무, spec §2).

회사마다 표시계정명이 달라도 `account_id`(표준계정ID)로 표준화한다. ★4 파생지표
(YoY/부채비율/회전율 등)의 입력이 된다.

⚠️ 이 사전의 범위·표기는 팀장님(DART 파트)과 합의할 1순위 항목이다(spec §6). 여기 값은
실착수를 막지 않기 위한 baseline 이며, 합의 후 확장/정정될 수 있다.
"""

from __future__ import annotations

# 표준계정ID(account_id) → 정규 metric 키.
ACCOUNT_ID_TO_METRIC: dict[str, str] = {
    "ifrs-full_Revenue": "revenue",
    "ifrs_Revenue": "revenue",
    "dart_OperatingIncomeLoss": "operating_income",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_Assets": "total_assets",
    "ifrs-full_Liabilities": "total_liabilities",
    "ifrs-full_Equity": "total_equity",
    "ifrs-full_Inventories": "inventories",
    "ifrs-full_TradeAndOtherCurrentReceivables": "trade_receivables",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment": "capex",
    "dart_InterestExpense": "interest_expense",
}

# account_id 가 비어있는 비표준 항목용 account_nm(한국어 표시계정) 폴백(spec §5).
ACCOUNT_NM_TO_METRIC: dict[str, str] = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "분기순이익": "net_income",
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "재고자산": "inventories",
    "매출채권": "trade_receivables",
    "매출채권및기타유동채권": "trade_receivables",
}


def map_account(account_id: str | None, account_nm: str | None) -> str | None:
    """account_id 우선, 없으면 account_nm 으로 정규 metric 키를 찾는다(없으면 None)."""
    if account_id:
        metric = ACCOUNT_ID_TO_METRIC.get(account_id.strip())
        if metric:
            return metric
    if account_nm:
        return ACCOUNT_NM_TO_METRIC.get(account_nm.strip())
    return None
