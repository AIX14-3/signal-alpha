"""특허 변동성 신호의 *경제가치*(vol-managed 샤프) 검정 드라이버.

배경: 특허→실현변동성 횡단면 신호는 방향/타이밍 알파가 아님이 확정됐다
(``ML/Patent/2026-07-01-handoff-magnitude.md`` §8·§9: embargo로 시점 누수 아님, within-firm
분해로 within_ic≈0 = **정적 특성**). 남은 질문은 "정적 vol 특성이 **리스크 사이징 도구**로서
경제가치가 있나"뿐. 이 스크립트가 그걸 정직하게(Deflated Sharpe·t≥3) 측정한다.

arm(``--arms all``, P1):
  1. buy&hold-EW        : equal-weight 매수보유(base)
  2. own-RV vol-managed : Moreira-Muir 고전 오버레이(이 유니버스서 시계열 vol-타이밍이 먹히나)
  3. patent-vol-managed : 특허 vol 예측 오버레이 — within_ic≈0이라 ~평탄→Sharpe≈base 예상(무신호 시연)
  4. patent inverse-vol : 특허 정적 vol로 횡단면 저변동 가중(정적 특성의 정직한 경제적 쓰임)
  5. combined           : inverse-vol 구성 + own-RV 오버레이
  (--oracle) LEAKY-ORACLE: 진짜 forward vol 랭킹 가중. **누수 진단용, 절대 트레이더블 아님.**

``--arms mm`` (P2): 1·2·3만(equal-weight 바스켓 시계열 vol-타이밍 격리).
P3(34종목): ``--tickers <34> --prices-csv prices34.csv`` 로 동일 재실행.

실행(services/agent-worker, **로컬 DB만**):
    export DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha
    python scripts/backfill_prices_fdr.py --tickers <13> --benchmark KS11 \
        --start 2021-01-01 --end 2023-12-31 --out prices13.csv
    python scripts/patent_vol_managed_backtest.py --prices-csv prices13.csv --trials 20
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # services/agent-worker (app.*)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))  # repo-root pkg

import numpy as np  # noqa: E402

from app.ml.research import patent_db  # noqa: E402
from app.ml.research.datalab_dataset import weekly_signal_dates  # noqa: E402
from app.ml.research.portfolio import (  # noqa: E402
    ArmResult,
    constituent_period_returns,
    equal_weight_basket,
    equal_weight_period_returns,
    inverse_vol_weights,
    sharpe_report,
    trailing_realized_variance,
    vol_managed,
    weighted_period_returns,
)
from app.ml.research.prices_csv import load_prices_csv  # noqa: E402

DEFAULT_TICKERS = (
    "005930,066570,005380,051910,012330,000660,204320,096770,035420,035720,068270,042700,000100"
)


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


# --------------------------------------------------------------------------- #
# 특허 vol 예측 추출 (누수 방지: ds.X의 look-ahead-safe 피처만; excess_returns=라벨은 oracle만)
# --------------------------------------------------------------------------- #
def _vol_score_by_date(
    ds, id_to_ticker: dict[int, str], feature: str, vol_sign: int, *, use_label: bool = False
) -> dict[date, dict[str, float]]:
    """날짜별 ``{ticker: vol_score∈(0,1]}`` — 예측 변동성에 단조 증가.

    각 날짜에서 ``vol_sign*feature`` 로 오름차순 랭크(1..n)/n. vol_sign<0이면 특허 多=저변동
    (§9 between_ic≈−0.42). ``use_label=True`` 는 ds.excess_returns(진짜 forward vol)로 랭크 =
    **누수 oracle 전용**.
    """
    if use_label:
        vals = np.asarray(ds.excess_returns, dtype=float)
    else:
        try:
            col = ds.feature_names.index(feature)
        except ValueError:
            raise SystemExit(
                f"--vol-feature '{feature}' not in features: {ds.feature_names}"
            )
        vals = np.asarray(ds.X[:, col], dtype=float)

    rows_by_date: dict[date, list[tuple[str, float]]] = {}
    for i in range(len(ds)):
        tkr = id_to_ticker.get(int(ds.stock_ids[i]))
        v = vals[i]
        if tkr is None or not np.isfinite(v):
            continue
        d = date.fromordinal(int(ds.dates[i]))
        rows_by_date.setdefault(d, []).append((tkr, v))

    out: dict[date, dict[str, float]] = {}
    for d, rows in rows_by_date.items():
        ordered = sorted(rows, key=lambda tv: vol_sign * tv[1])  # 예측 vol 오름차순
        n = len(ordered)
        out[d] = {tkr: (rank + 1) / n for rank, (tkr, _) in enumerate(ordered)}
    return out


# --------------------------------------------------------------------------- #
# arm 구성
# --------------------------------------------------------------------------- #
def _build_arms(args, prices_by_ticker, ds, id_to_ticker) -> list[ArmResult]:
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    bench = prices_by_ticker.get(args.benchmark)
    if bench is None:
        raise SystemExit(f"benchmark {args.benchmark} not in prices CSV.")

    lo, hi = date.fromisoformat(args.start), date.fromisoformat(args.end)
    master = [d for d in bench.dates if lo <= d <= hi]
    rebalance_dates = weekly_signal_dates(master, step=args.signal_step)
    if len(rebalance_dates) < 3:
        raise SystemExit(f"too few rebalance dates ({len(rebalance_dates)}) — widen the range.")
    n_periods = len(rebalance_dates) - 1
    ppy = 252.0 / args.signal_step
    starts = rebalance_dates[:n_periods]  # 기간 i의 시작(정보 기지 시점)

    cpr = constituent_period_returns(prices_by_ticker, tickers, rebalance_dates)
    ew_period = equal_weight_period_returns(cpr, tickers)
    ew_daily = equal_weight_basket(prices_by_ticker, tickers)

    # own-RV 분산 예측(각 기간 시작 시점 후행 실현분산)
    own_vf = np.asarray(
        [
            trailing_realized_variance(ew_daily, s, args.lookback) or np.nan
            for s in starts
        ],
        dtype=float,
    )

    def report(returns, name):
        return sharpe_report(
            returns,
            name=name,
            periods_per_year=ppy,
            n_trials=args.trials,
            tstat_threshold=args.tstat_threshold,
        )

    arms: list[ArmResult] = [
        report(ew_period, "buy&hold-EW"),
        report(vol_managed(ew_period, own_vf), "own-RV vol-managed"),
    ]

    # 특허 vol 예측
    vsd = _vol_score_by_date(ds, id_to_ticker, args.vol_feature, args.vol_sign)
    # patent-vol-managed: 바스켓 수준 특허 vol(구성종목 평균)² 을 분산 예측으로
    pat_vf = np.full(n_periods, np.nan)
    for i, s in enumerate(starts):
        day = vsd.get(s, {})
        scores = [day[t] for t in tickers if t in day]
        if scores:
            m = sum(scores) / len(scores)
            pat_vf[i] = m * m
    arms.append(report(vol_managed(ew_period, pat_vf), "patent-vol-managed"))

    if args.arms == "all":
        # patent inverse-vol 가중(저변동 과중), 결측 날짜는 equal fallback
        weights, fallbacks = [], 0
        for s in starts:
            day = vsd.get(s, {})
            scores = {t: day.get(t, float("nan")) for t in tickers}
            valid = sum(1 for v in scores.values() if np.isfinite(v) and v > 0)
            if valid < 2:
                fallbacks += 1
            weights.append(inverse_vol_weights(scores))
        inv_period = weighted_period_returns(cpr, tickers, weights)
        arms.append(report(inv_period, "patent inverse-vol"))
        arms.append(report(vol_managed(inv_period, own_vf), "combined(inv+ownRV)"))
        if fallbacks:
            print(f"  [note] inverse-vol fell back to equal-weight on {fallbacks}/{n_periods} periods")

        if args.oracle:
            osd = _vol_score_by_date(ds, id_to_ticker, args.vol_feature, +1, use_label=True)
            ow = [
                inverse_vol_weights({t: osd.get(s, {}).get(t, float("nan")) for t in tickers})
                for s in starts
            ]
            arms.append(report(weighted_period_returns(cpr, tickers, ow), "LEAKY-ORACLE(inv)"))

    return arms


# --------------------------------------------------------------------------- #
async def _load_dataset(args, database_url):
    """stock_id↔ticker 맵 + realized_vol Dataset(xs_normalize=none, 원 특허피처)."""
    from signal_alpha_data_access import DatabaseSettings, create_pool

    from app.ml.research.datalab_db import resolve_stock_ids

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    pool = await create_pool(DatabaseSettings(database_url=database_url))
    try:
        async with pool.acquire() as conn:
            id_by_ticker = await resolve_stock_ids(conn, tickers)
    finally:
        await pool.close()
    id_to_ticker = {v: k for k, v in id_by_ticker.items()}

    ds = await patent_db.load_from_env(
        database_url=database_url,
        tickers=tickers,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        benchmark_ticker=args.benchmark,
        prices_csv=args.prices_csv,
        target="realized_vol",
        xs_normalize="none",
        lookback_days=args.lookback_days,
        horizon_sessions=args.horizon,
        signal_step=args.signal_step,
        min_cross_section=args.min_cross_section,
    )
    return id_to_ticker, ds


def _print_table(arms: list[ArmResult], args) -> None:
    print()
    print(f"=== patent vol-managed backtest ({args.arms}, {args.benchmark}, trials={args.trials}) ===")
    print(f"{'arm':<22}{'n':>4}{'ann_Sharpe':>12}{'t-stat':>9}{'DSR':>8}{'pass@t>=' + str(args.tstat_threshold):>11}")
    print("-" * 66)
    for a in arms:
        print(
            f"{a.name:<22}{a.n_periods:>4}{a.ann_sharpe:>12.3f}{a.tstat:>9.2f}"
            f"{a.dsr:>8.3f}{('YES' if a.passes else 'no'):>11}"
        )
    print("-" * 66)
    print("DSR = Deflated Sharpe (Bailey·López de Prado): 1에 가까울수록 다중검정 후에도 유의.")
    print("판정: own-RV가 buy&hold 대비 개선되나 / patent-*가 own-RV·EW 대비 증분 있나(§9상 없음 예상).")


def main() -> None:
    p = argparse.ArgumentParser(description="특허 vol-managed 오버레이 샤프 경제가치 검정")
    p.add_argument("--tickers", default=DEFAULT_TICKERS)
    p.add_argument("--prices-csv", default="prices13.csv")
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2023-12-31")
    p.add_argument("--horizon", type=int, default=5, help="라벨 forward vol horizon(세션)")
    p.add_argument("--signal-step", type=int, default=20, help="리밸런스 간격(세션, 월별≈20)")
    p.add_argument("--lookback", type=int, default=60, help="own-RV 후행 분산 창(거래일)")
    p.add_argument("--lookback-days", type=int, default=180, help="특허 피처 룩백(일)")
    p.add_argument("--min-cross-section", type=int, default=6)
    p.add_argument("--trials", type=int, default=20, help="DSR 다중검정 trial 수(전 실험 config 총수 반영)")
    p.add_argument("--vol-feature", default="patent__total", help="vol proxy 특허 피처")
    p.add_argument("--vol-sign", type=int, default=-1, choices=(-1, 1),
                   help="피처→vol 부호(§9 between_ic<0이라 기본 -1: 특허 多=저변동)")
    p.add_argument("--tstat-threshold", type=float, default=3.0)
    p.add_argument("--arms", choices=("all", "mm"), default="all",
                   help="all=5arm 정직판(P1); mm=엄격 Moreira-Muir(1·2·3만, P2)")
    p.add_argument("--oracle", action="store_true", help="누수 oracle arm 추가(진단용)")
    args = p.parse_args()

    try:  # Windows 콘솔(cp949)에서도 한글·액센트 안전 출력
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL 미설정 — 로컬 docker DB를 지정하세요.")
    _require_local(database_url)

    prices_by_ticker = load_prices_csv(args.prices_csv)
    id_to_ticker, ds = asyncio.run(_load_dataset(args, database_url))
    print(f"loaded dataset: {len(ds)} rows, {len(id_to_ticker)} stocks, {len(ds.feature_names)} features; dropped={dict(ds.dropped)}")

    arms = _build_arms(args, prices_by_ticker, ds, id_to_ticker)
    _print_table(arms, args)


if __name__ == "__main__":
    main()
