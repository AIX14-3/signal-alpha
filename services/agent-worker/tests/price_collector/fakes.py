from __future__ import annotations

from app.collectors.price.schemas import InvestorFlow, StockSnapshot, TargetStock


def ka10001_payload(**overrides: object) -> dict:
    payload = {
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
        "stk_cd": "005930",
        "stk_nm": "삼성전자",
        "cur_prc": "+74300",
        "open_pric": "73800",
        "high_pric": "+74500",
        "low_pric": "-73600",
        "trde_qty": "12345678",
        "trde_prica": "915000",
        "mac": "4435000",
        "flo_stk": "5969783",
        "per": "13.45",
        "pbr": "1.32",
        "eps": "5523",
        "bps": "56254",
        "roe": "9.81",
        "roa": "6.12",
    }
    payload.update(overrides)
    return payload


def ka10059_payload(rows: list[dict] | None = None) -> dict:
    return {
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
        "stk_invsr_orgn": rows
        if rows is not None
        else [
            {
                "dt": "20260611",
                "ind_invsr": "-120000",
                "frgnr_invsr": "+95000",
                "orgn": "+25000",
            },
            {
                "dt": "20260610",
                "ind_invsr": "+10000",
                "frgnr_invsr": "-5000",
                "orgn": "-5000",
            },
        ],
    }


class FakeRestClient:
    """Returns canned payloads per (api_id, ticker); raises configured errors."""

    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], dict] = {}
        self.errors: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, dict]] = []

    async def request(self, api_id: str, body: dict, *, path: str = "") -> dict:
        self.calls.append((api_id, body))
        ticker = str(body.get("stk_cd", ""))
        if (api_id, ticker) in self.errors:
            raise self.errors[(api_id, ticker)]
        return self.payloads[(api_id, ticker)]


class FakeRepository:
    """In-memory PriceSnapshotRepository double."""

    def __init__(self, targets: list[TargetStock]) -> None:
        self.targets = targets
        self.snapshots: list[tuple[int, StockSnapshot]] = []
        self.ohlcv_upserts: list[tuple[int, StockSnapshot]] = []
        self.flow_updates: list[tuple[int, InvestorFlow]] = []
        self.ohlcv_rows: set[tuple[int, object]] = set()

    async def list_target_stocks(self) -> list[TargetStock]:
        return self.targets

    async def insert_snapshot(self, stock_id: int, snapshot: StockSnapshot) -> None:
        self.snapshots.append((stock_id, snapshot))

    async def upsert_intraday_ohlcv(self, stock_id: int, snapshot: StockSnapshot) -> bool:
        if not snapshot.has_full_ohlc():
            return False
        self.ohlcv_upserts.append((stock_id, snapshot))
        self.ohlcv_rows.add((stock_id, snapshot.trade_date))
        return True

    async def update_investor_flow(self, stock_id: int, flow: InvestorFlow) -> bool:
        if (stock_id, flow.trade_date) not in self.ohlcv_rows:
            return False
        self.flow_updates.append((stock_id, flow))
        return True
