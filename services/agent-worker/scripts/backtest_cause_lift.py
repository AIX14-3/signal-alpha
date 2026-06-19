"""Backtest harness: does the DataLab `cause` tag carry forward-return lift?

Measures whether conditioning on the cause tag (catalyst / fomo / price_led)
separates forward returns — i.e. whether cause adds predictive value over the
cause-agnostic baseline (협업안 §4 검증; docs §10 "측정 후에야 정확도 주장").

Procedure (see the plan): build a point-in-time panel of cause-tagged DATALAB
signals → join +k-trading-day forward returns from ohlcv_data → group by cause →
report n / mean return / hit-rate → permutation test (catalyst vs baseline). A
hard N-gate refuses to report a "lift" when any group is too small.

Real mode reads cause off ``agent_results.method_detail`` (what the pipeline
persists). ``--smoke`` fabricates a labeled panel over PAST dates against the REAL
backfilled prices to prove the return/grouping/bootstrap math runs end-to-end —
clearly NOT a lift claim.

Run (from services/agent-worker, LOCAL DB only):
    $env:DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    uv run python scripts/backtest_cause_lift.py            # real panel (N-gates here)
    uv run python scripts/backtest_cause_lift.py --smoke     # logic smoke on real prices
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from bisect import bisect_left
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))

import asyncpg

WINDOWS = (5, 20, 60)        # forward trading-day horizons
MIN_N_PER_GROUP = 30         # N-gate: refuse a lift claim below this per group
BOOTSTRAP_ITERS = 2000
CAUSES = ("catalyst", "fomo", "price_led")


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #
async def load_price_series(conn) -> dict[int, tuple[list[date], list[float]]]:
    """{stock_id: (ascending dates, closes)} for forward-return lookups."""
    rows = await conn.fetch(
        "SELECT stock_id, trade_date, close FROM ohlcv_data ORDER BY stock_id, trade_date"
    )
    series: dict[int, tuple[list[date], list[float]]] = {}
    for r in rows:
        d, c = series.setdefault(int(r["stock_id"]), ([], []))
        d.append(r["trade_date"])
        c.append(float(r["close"]))
    return series


async def load_cause_panel(conn) -> list[dict]:
    """PIT panel from persisted DATALAB cause tags (production source)."""
    rows = await conn.fetch(
        """
        SELECT fs.stock_id, fs.signal_date, agr.method_detail->>'cause' AS cause
        FROM final_signals fs
        JOIN agent_results agr ON agr.result_id = fs.analysis_result_id
        WHERE agr.debate_method = 'D-3'
          AND agr.method_detail ? 'cause'
        """
    )
    return [{"stock_id": int(r["stock_id"]), "date": r["signal_date"], "cause": r["cause"]}
            for r in rows]


def forward_return(series, stock_id: int, t0: date, k: int) -> float | None:
    """+k trading-day return from the first session on/after t0. None if the
    window has not elapsed yet (so unrealized futures never pollute the result)."""
    entry = series.get(stock_id)
    if not entry:
        return None
    dates, closes = entry
    i0 = bisect_left(dates, t0)
    if i0 >= len(dates) or i0 + k >= len(dates):
        return None
    base = closes[i0]
    return (closes[i0 + k] / base - 1.0) if base else None


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _hit_rate(xs: list[float]) -> float:
    return (sum(1 for x in xs if x > 0) / len(xs)) if xs else float("nan")


def permutation_pvalue(panel: list[tuple[str, float]], iters: int) -> float | None:
    """Two-sided permutation test: catalyst mean return vs the whole baseline.

    Deterministic shuffle (no RNG): rotate the label list by a stride each iter,
    so the test is reproducible across runs without a seed.
    """
    labels = [c for c, _ in panel]
    rets = [r for _, r in panel]
    cats = [r for c, r in panel if c == "catalyst"]
    if not cats or len(set(labels)) < 2:
        return None
    observed = abs(_mean(cats) - _mean(rets))
    n = len(rets)
    extreme = 0
    for i in range(1, iters + 1):
        stride = 1 + (i % (n - 1)) if n > 1 else 1
        rotated = labels[stride:] + labels[:stride]
        shuffled_cats = [rets[j] for j in range(n) if rotated[j] == "catalyst"]
        if shuffled_cats and abs(_mean(shuffled_cats) - _mean(rets)) >= observed:
            extreme += 1
    return extreme / iters


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def report(panel: list[dict], series, *, smoke: bool) -> None:
    print("\n" + "=" * 64)
    print("DataLab cause 백테스트 lift" + ("  [SMOKE — 로직 검증용, lift 주장 아님]" if smoke else ""))
    print("=" * 64)
    cover = {sid: (d[0][0], d[0][-1], len(d[0])) for sid, d in series.items() if d[0]}
    print(f"가격 커버리지: {len(cover)}개 종목"
          + (f", 예: {next(iter(cover.values()))}" if cover else ""))
    print(f"cause-tag 신호 패널: {len(panel)}건")
    if not panel:
        print("\n▶ 측정 불가: cause-tag된 DATALAB 신호가 0건.")
        print("  블로커 = 신호측 데이터(로컬 DataLab=1카테고리·13일·2026-05~06, 최근이라 forward 윈도 미경과;")
        print("  실제 DataLab은 prod-locked·stale). 가격은 확보됨. 실데이터 유입 시 이 스크립트 그대로 재실행.")
        return

    for k in WINDOWS:
        groups: dict[str, list[float]] = {c: [] for c in (*CAUSES, "__baseline__")}
        for row in panel:
            r = forward_return(series, row["stock_id"], row["date"], k)
            if r is None:
                continue
            groups["__baseline__"].append(r)
            if row["cause"] in groups:
                groups[row["cause"]].append(r)

        print(f"\n[+{k}일] 실현수익 있는 표본 (excess 미적용 — 후속):")
        for g in (*CAUSES, "__baseline__"):
            xs = groups[g]
            name = "baseline(전체)" if g == "__baseline__" else g
            if xs:
                print(f"  {name:16} n={len(xs):4d}  평균={_mean(xs)*100:+6.2f}%  적중={_hit_rate(xs)*100:5.1f}%")
            else:
                print(f"  {name:16} n=   0")

        base_n = len(groups["__baseline__"])
        small = [g for g in ("catalyst", "fomo") if len(groups[g]) < MIN_N_PER_GROUP]
        if base_n < MIN_N_PER_GROUP or small:
            print(f"  ▶ N-게이트: 표본 부족(min {MIN_N_PER_GROUP}/그룹) → lift 판정 보류"
                  f" [baseline={base_n}, 부족그룹={small or '-'}]")
            continue
        lift = _mean(groups["catalyst"]) - _mean(groups["__baseline__"])
        p = permutation_pvalue([(row["cause"], forward_return(series, row["stock_id"], row["date"], k))
                                for row in panel
                                if forward_return(series, row["stock_id"], row["date"], k) is not None], BOOTSTRAP_ITERS)
        print(f"  ▶ lift(catalyst − baseline) = {lift*100:+.2f}%p,  순열검정 p={p:.3f}"
              if p is not None else f"  ▶ lift = {lift*100:+.2f}%p (p 계산불가)")


# --------------------------------------------------------------------------- #
# smoke panel (PAST dates, real prices) — proves the math runs, NOT a lift claim
# --------------------------------------------------------------------------- #
def smoke_panel(series) -> list[dict]:
    panel: list[dict] = []
    sample_dates = [date(2024, 3, 4), date(2024, 6, 3), date(2024, 9, 2), date(2025, 1, 6),
                    date(2025, 4, 1), date(2025, 7, 1), date(2025, 10, 1), date(2026, 1, 5)]
    causes_cycle = (*CAUSES, "catalyst", "fomo", "price_led", "catalyst", "fomo")
    i = 0
    for stock_id in sorted(series):
        for d in sample_dates:
            panel.append({"stock_id": stock_id, "date": d, "cause": causes_cycle[i % len(causes_cycle)]})
            i += 1
    return panel


async def main(smoke: bool) -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL required (LOCAL docker DB).")
    _require_local(db_url)
    conn = await asyncpg.connect(db_url)
    try:
        series = await load_price_series(conn)
        panel = smoke_panel(series) if smoke else await load_cause_panel(conn)
        report(panel, series, smoke=smoke)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="fabricate a past-dated panel to verify the math")
    args = ap.parse_args()
    asyncio.run(main(args.smoke))
