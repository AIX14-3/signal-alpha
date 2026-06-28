"""주가 BASE 모델(src_price) 학습 — OHLCV 밀집 일별 패널 (C안).

``train_source_models`` 의 event_study_panel(signal_events 기반) 라벨은 이벤트 소스용이다.
주가는 이벤트가 아니라 **밀집 일별 패널**로 학습하는 게 자연스럽다(데이터 풍부). 이 모듈은
``ohlcv_data`` 에서 직접 ``(stock, asof, fwd_return)`` 표본을 만들어, 동일한
``train_and_save(source="price")`` 로 walk-forward OOF 학습한다(LightGBM CPU).

look-ahead 0: 각 asof 의 피처는 ``trade_date <= asof`` 행만(과거+당일), 라벨은 미래 horizon
세션 수익률. 자기상관 완화를 위해 ``step`` 거래일마다 표본을 뽑는다.

사용:
    uv run python -m app.ml.train_price_model --database-url <DSN>
        [--horizon 20] [--step 5] [--min-history 65] [--asof-from YYYY-MM-DD] [--asof-to YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
from datetime import date
from typing import Any

from app.ml.source_features import price_features
from app.ml.train_source_models import train_and_save

DEFAULT_HORIZON = 20  # fwd_return_20d 와 정렬
DEFAULT_MIN_HISTORY = 65  # sma60 + 여유(스케일-프리 피처가 충분히 채워지는 최소 세션)
DEFAULT_STEP = 5  # 매 5 거래일마다 asof 표본(중첩/자기상관 완화)

_OHLCV_SQL = (
    "SELECT stock_id, trade_date, close, volume, foreign_net, institution_net "
    "FROM ohlcv_data ORDER BY stock_id, trade_date"
)


async def collect_price_samples(
    connection: Any,
    *,
    horizon: int = DEFAULT_HORIZON,
    min_history: int = DEFAULT_MIN_HISTORY,
    step: int = DEFAULT_STEP,
    asof_from: date | None = None,
    asof_to: date | None = None,
) -> list[dict]:
    """ohlcv_data → ``[{asof, features, label}]`` 밀집 표본(전 종목 패널 풀링, D1)."""
    rows = await connection.fetch(_OHLCV_SQL)
    by_stock: dict[int, list[dict]] = {}
    for row in rows:
        by_stock.setdefault(int(row["stock_id"]), []).append(dict(row))

    samples: list[dict] = []
    for srows in by_stock.values():
        closes = [float(r["close"]) for r in srows]
        n = len(srows)
        # i = asof 인덱스: 앞쪽 min_history 세션(피처) + 뒤쪽 horizon 세션(라벨) 확보.
        for i in range(min_history - 1, n - horizon, step):
            asof = srows[i]["trade_date"]
            if not isinstance(asof, date):
                asof = date.fromisoformat(str(asof)[:10])
            if asof_from and asof < asof_from:
                continue
            if asof_to and asof > asof_to:
                continue
            base = closes[i]
            if not base:
                continue
            label = closes[i + horizon] / base - 1.0
            if not math.isfinite(label):
                continue
            features = price_features(srows[: i + 1])  # PIT: 과거+당일만
            samples.append({"asof": asof, "features": features, "label": label})
    return samples


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = args.database_url or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL 이 필요합니다 (--database-url 또는 환경변수).")

    connection = await asyncpg.connect(dsn)
    try:
        samples = await collect_price_samples(
            connection,
            horizon=args.horizon,
            min_history=args.min_history,
            step=args.step,
            asof_from=_to_date(args.asof_from),
            asof_to=_to_date(args.asof_to),
        )
    finally:
        await connection.close()

    print(f"[src_price] collected {len(samples)} samples")
    report = train_and_save(samples, source="price", out_dir=args.out_dir, n_splits=args.n_splits)
    print(f"[src_price] {report}")
    return 0


def _to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description="주가 BASE 모델(src_price) OHLCV 밀집 패널 학습")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--step", type=int, default=DEFAULT_STEP)
    parser.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    parser.add_argument("--asof-from", default=None, help="표본 asof 시작(YYYY-MM-DD)")
    parser.add_argument("--asof-to", default=None, help="표본 asof 끝(YYYY-MM-DD)")
    parser.add_argument("--n-splits", type=int, default=4)
    from app.ml.source_inference import DEFAULT_ARTIFACT_DIR

    parser.add_argument("--out-dir", default=str(DEFAULT_ARTIFACT_DIR))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
