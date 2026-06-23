"""KOSPI20 벤치마크 CSV(vol-benchmark/raw) → signal-alpha DB 적재 (멱등).

3년치 KOSPI 시총 상위 20종목 수집본(OHLCV·투자자순매수·프로그램매매·환율, docs
benchmark-kospi20)을 signal-alpha 파이프라인 DB에 적재해 ML/DL(변동성) 추론이 실제
데이터를 보게 한다. 동일 입력 재실행은 ON CONFLICT 로 멱등.

  - stocks       : 20종목 upsert(is_target=TRUE)
  - ohlcv_data   : OHLCV + 외국인/기관/개인 순매수 (UNIQUE stock_id,trade_date)
  - program_trading / fx_rates : 신규 테이블 (023_kospi20_benchmark_data 마이그레이션)

사용:
    python tools/load_kospi20_benchmark.py \
        --database-url postgresql://signal_alpha:...@localhost:5432/signal_alpha \
        --raw-dir C:/Users/biop9/vol-benchmark/raw

DATABASE_URL 결정 순서: --database-url > 환경변수 DATABASE_URL > 루트 .env.
RAW 경로: --raw-dir > 환경변수 KOSPI20_RAW_DIR > ../vol-benchmark/raw.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg  # type: ignore[import]

ROOT = Path(__file__).resolve().parents[1]

# KOSPI 시총 top-20 (보통주) — vol-benchmark/tools collect_kospi20.py 와 동일 유니버스.
TICKERS = [
    ("000660", "SK하이닉스"), ("005930", "삼성전자"), ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"), ("012450", "한화에어로스페이스"), ("005380", "현대차"),
    ("009150", "삼성전기"), ("034020", "두산에너빌리티"), ("000270", "기아"),
    ("032830", "삼성생명"), ("105560", "KB금융"), ("068270", "셀트리온"),
    ("035420", "NAVER"), ("012330", "현대모비스"), ("055550", "신한지주"),
    ("042660", "한화오션"), ("329180", "HD현대중공업"), ("028260", "삼성물산"),
    ("015760", "한국전력"), ("267260", "HD현대일렉트릭"),
]

_EMPTY = {"", "nan", "none", "null"}


def _resolve_db_url(arg: str | None) -> str:
    if arg:
        return arg
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("DATABASE_URL을 찾을 수 없습니다 (--database-url 옵션을 쓰세요).")


def _rows(path: Path):
    if not path.is_file():
        raise SystemExit(f"CSV 없음: {path}")
    with path.open(encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh)


def _i(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(float(value)) if value.lower() not in _EMPTY else None


def _d(value: str | None) -> Decimal | None:
    value = (value or "").strip()
    return Decimal(value) if value.lower() not in _EMPTY else None


def _f(value: str | None) -> float | None:
    value = (value or "").strip()
    return float(value) if value.lower() not in _EMPTY else None


def _date(value: str) -> date:
    return date.fromisoformat(value.strip())


async def _upsert_stocks(conn) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for ticker, name in TICKERS:
        sid = await conn.fetchval(
            """
            INSERT INTO stocks (ticker, name, market, is_active, is_target)
            VALUES ($1, $2, 'KOSPI', TRUE, TRUE)
            ON CONFLICT (ticker) DO UPDATE SET is_target = TRUE
            RETURNING id
            """,
            ticker,
            name,
        )
        mapping[ticker] = int(sid)
    return mapping


async def _load_ohlcv(conn, raw: Path, smap: dict[str, int]) -> int:
    rows = [
        (smap[r["ticker"]], _date(r["date"]), _d(r["open"]), _d(r["high"]),
         _d(r["low"]), _d(r["close"]), _i(r["volume"]))
        for r in _rows(raw / "kospi20_ohlcv.csv")
        if r["ticker"] in smap
    ]
    await conn.executemany(
        """
        INSERT INTO ohlcv_data (stock_id, trade_date, open, high, low, close, volume)
        VALUES ($1, $2::date, $3, $4, $5, $6, $7)
        ON CONFLICT (stock_id, trade_date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, volume = EXCLUDED.volume
        """,
        rows,
    )
    return len(rows)


async def _load_investor(conn, raw: Path, smap: dict[str, int]) -> int:
    # 수급은 OHLCV가 있는 (종목,일) 행에만 붙인다 — OHLCV 없는 날짜는 변동성 입력이 아니므로 skip.
    rows = [
        (smap[r["ticker"]], _date(r["date"]), _i(r["foreign_net"]), _i(r["inst_net"]), _i(r["indiv_net"]))
        for r in _rows(raw / "kospi20_investor.csv")
        if r["ticker"] in smap
    ]
    await conn.executemany(
        """
        UPDATE ohlcv_data
           SET foreign_net = $3, institution_net = $4, individual_net = $5
         WHERE stock_id = $1 AND trade_date = $2::date
        """,
        rows,
    )
    return len(rows)


async def _load_program(conn, raw: Path, smap: dict[str, int]) -> int:
    rows = [
        (smap[r["ticker"]], _date(r["date"]), _i(r["prog_buy_qty"]), _i(r["prog_sell_qty"]),
         _i(r["prog_net_qty"]), _i(r["prog_net_amt"]))
        for r in _rows(raw / "kospi20_program.csv")
        if r["ticker"] in smap
    ]
    await conn.executemany(
        """
        INSERT INTO program_trading
            (stock_id, trade_date, prog_buy_qty, prog_sell_qty, prog_net_qty, prog_net_amt)
        VALUES ($1, $2::date, $3, $4, $5, $6)
        ON CONFLICT (stock_id, trade_date) DO UPDATE SET
            prog_buy_qty = EXCLUDED.prog_buy_qty, prog_sell_qty = EXCLUDED.prog_sell_qty,
            prog_net_qty = EXCLUDED.prog_net_qty, prog_net_amt = EXCLUDED.prog_net_amt
        """,
        rows,
    )
    return len(rows)


async def _load_fx(conn, raw: Path) -> int:
    rows = [
        ("USD/KRW", _date(r["date"]), _f(r["usdkrw_rate"]), _f(r["usdkrw_mid"]))
        for r in _rows(raw / "fx_usdkrw.csv")
    ]
    await conn.executemany(
        """
        INSERT INTO fx_rates (pair, trade_date, rate, mid)
        VALUES ($1, $2::date, $3, $4)
        ON CONFLICT (pair, trade_date) DO UPDATE SET rate = EXCLUDED.rate, mid = EXCLUDED.mid
        """,
        rows,
    )
    return len(rows)


async def main() -> None:
    ap = argparse.ArgumentParser(description="KOSPI20 벤치마크 CSV → signal-alpha DB 적재")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--raw-dir", default=None)
    args = ap.parse_args()

    raw = Path(args.raw_dir or os.getenv("KOSPI20_RAW_DIR") or (ROOT.parent / "vol-benchmark" / "raw"))
    if not raw.is_dir():
        raise SystemExit(f"raw 디렉터리 없음: {raw} (--raw-dir 로 지정)")

    conn = await asyncpg.connect(_resolve_db_url(args.database_url))
    try:
        smap = await _upsert_stocks(conn)
        n_ohlcv = await _load_ohlcv(conn, raw, smap)
        n_inv = await _load_investor(conn, raw, smap)
        n_prog = await _load_program(conn, raw, smap)
        n_fx = await _load_fx(conn, raw)
    finally:
        await conn.close()

    print(
        f"적재 완료 — stocks={len(smap)} ohlcv={n_ohlcv} investor={n_inv} "
        f"program={n_prog} fx={n_fx}"
    )


if __name__ == "__main__":
    asyncio.run(main())
