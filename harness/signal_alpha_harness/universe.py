"""KOSPI200 universe loaded from a committed snapshot CSV.

신뢰도 우선 재설계(2026-06-11)에 따라 30종목 수기 리스트를 KOSPI200 스냅샷으로
교체했다. 스냅샷은 ``snapshot_universe.py``가 pykrx로 생성해 ``data/``에
**버전 파일로 커밋**한다 — 실험 로그(experiments.jsonl)는 고정된 유니버스
파일에 대해서만 의미가 있다.

알려진 한계 (보고서에 반드시 명시): KRX는 과거 임의 시점의 KOSPI200 구성내역을
제공하지 않으므로 이 스냅샷은 **조회일 기준 생존 종목**이다. 과거 구간 백테스트에는
생존 편향이 있으며, 최종 신뢰도 증거는 포워드 섀도 테스트(편향 0%)가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
UNIVERSE_GLOB = "universe_kospi200_*.csv"

WATCH_TICKER = "000660"  # SK하이닉스 (상세 관찰 종목 유지)


@dataclass(frozen=True)
class UniverseStock:
    ticker: str
    name: str


def latest_snapshot_path(data_dir: Path = DATA_DIR) -> Path:
    candidates = sorted(data_dir.glob(UNIVERSE_GLOB))
    if not candidates:
        raise FileNotFoundError(
            f"{data_dir}에 {UNIVERSE_GLOB} 스냅샷이 없습니다 — "
            "`uv run python -m signal_alpha_harness.snapshot_universe`로 생성 후 커밋하세요."
        )
    return candidates[-1]  # 파일명에 YYYYMMDD가 들어가므로 사전순 = 최신


def load_universe(path: Path | str | None = None) -> list[UniverseStock]:
    import csv

    snapshot = Path(path) if path is not None else latest_snapshot_path()
    stocks: list[UniverseStock] = []
    with snapshot.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            stocks.append(UniverseStock(ticker=row["ticker"], name=row["name"]))
    if not stocks:
        raise ValueError(f"유니버스 스냅샷이 비어 있습니다: {snapshot}")
    return stocks


def tickers(path: Path | str | None = None) -> list[str]:
    return [stock.ticker for stock in load_universe(path)]


def by_ticker(path: Path | str | None = None) -> dict[str, UniverseStock]:
    return {stock.ticker: stock for stock in load_universe(path)}
