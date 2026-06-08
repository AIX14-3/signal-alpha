"""In-memory fakes so the collector can be tested without Kiwoom or PostgreSQL."""

from app.schemas.price import OhlcvRow


class FakeKiwoomClient:
    """Returns canned TR records keyed by ``tr_code``."""

    def __init__(self, responses: dict[str, list[dict[str, str]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def request(
        self,
        tr_code: str,
        output: str,
        **inputs: str
    ) -> list[dict[str, str]]:
        self.calls.append((tr_code, inputs))
        return list(self._responses.get(tr_code, []))


class FakeOhlcvRepository:
    """Records upserts and run lifecycle in memory."""

    def __init__(self, stock_ids: dict[str, int]) -> None:
        self._stock_ids = stock_ids
        self.upserts: dict[int, list[OhlcvRow]] = {}
        self.runs: list[dict[str, object]] = []
        self.finished: list[dict[str, object]] = []

    def resolve_stock_id(self, ticker: str) -> int | None:
        return self._stock_ids.get(ticker)

    def upsert_ohlcv(self, stock_id: int, rows: list[OhlcvRow]) -> int:
        self.upserts.setdefault(stock_id, []).extend(rows)
        return len(rows)

    def start_run(self, run_mode: str) -> int:
        run_id = len(self.runs) + 1
        self.runs.append({"id": run_id, "run_mode": run_mode})
        return run_id

    def finish_run(
        self,
        run_id: int,
        status: str,
        collected_count: int,
        inserted_count: int,
        failed_count: int,
        error_message: str | None = None
    ) -> None:
        self.finished.append(
            {
                "id": run_id,
                "status": status,
                "collected_count": collected_count,
                "inserted_count": inserted_count,
                "failed_count": failed_count,
                "error_message": error_message
            }
        )
