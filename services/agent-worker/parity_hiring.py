"""Hiring parity check: legacy ``hiring_signals`` vs new AlternativeSignal harness.

Before retiring the legacy daily pipeline (``script/run_daily_hiring_pipeline.py``
→ ``hiring_analyzer.py`` → ``hiring_signals``) in favour of the new pure-analyzer
harness (``run_analyzers.py`` → registry → ``hiring/analyzer.py`` → final_signals),
this script runs BOTH for the same stocks/date and reports whether they agree on
the *direction* of the hiring read.

Why direction, not raw numbers: the two outputs live on different scales —
  legacy : relative_strength % (today vs 14-day baseline) + is_spike (≥150%)
  new    : score ∈ [-1, +1] + direction (positive/negative/neutral)
so exact numeric parity is impossible. Parity = do they bucket the same stock to
UP / FLAT / DOWN. Mismatches are the cutover risk list.

READ-ONLY: nothing is written. The new side skips persistence; the legacy side's
``analyze_hiring_trend`` is run with its ``INSERT INTO hiring_signals`` intercepted
(captured, not executed), so the real legacy formula is reused with zero DB writes.
Safe to point at production via DATABASE_URL.

Time-semantics caveat: the legacy analyzer only scores stocks with postings ON the
exact ``--date`` (observed_date = date), while the new analyzer scores over a
lookback window. So a stock with no posting precisely on --date shows as legacy
NO_DATA even though the new side has a signal — reported as PARTIAL, not a mismatch.

Usage:
    python parity_hiring.py                      # today, all hiring stocks
    python parity_hiring.py --date 2026-06-12
    python parity_hiring.py --ticker 005930
    python parity_hiring.py --json parity.json   # also dump machine-readable rows
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))

from app.analyzers.hiring.hiring_analyzer import HiringAnalyzer as LegacyHiringAnalyzer
from app.analyzers.registry import build_registry
from run_analyzers import fetch_target_stocks, load_env, parse_dsn, resolve_ssl

# ── Pure bucketing / comparison logic (unit-testable, no I/O) ────────────────

UP, FLAT, DOWN, NO_DATA = "UP", "FLAT", "DOWN", "NO_DATA"


def legacy_bucket(
    relative_strength: float | None,
    is_spike: bool,
    *,
    down_below: float = 100.0,
    up_at: float = 150.0,
) -> str:
    """Legacy hiring_signals row → coarse direction.

    is_spike (or relative_strength ≥ up_at) is the legacy 'surge' = UP.
    Below the baseline (relative_strength < down_below, i.e. < 100%) = DOWN.
    Around the baseline = FLAT. ``None`` strength = no legacy row for the date.
    """
    if relative_strength is None:
        return NO_DATA
    if is_spike or relative_strength >= up_at:
        return UP
    if relative_strength < down_below:
        return DOWN
    return FLAT


def new_bucket(direction: str | None) -> str:
    """New analyzer SourceResult.direction → coarse direction."""
    if direction == "positive":
        return UP
    if direction == "negative":
        return DOWN
    if direction == "neutral":
        return FLAT
    return NO_DATA  # 'unknown' or missing = no usable hiring signal


def verdict(legacy_b: str, new_b: str) -> str:
    """MATCH / MISMATCH when both have data; PARTIAL when one side has none."""
    if legacy_b == NO_DATA or new_b == NO_DATA:
        return "PARTIAL"
    return "MATCH" if legacy_b == new_b else "MISMATCH"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate verdicts. agreement_rate is over MATCH+MISMATCH (both-have-data)."""
    counts = {"MATCH": 0, "MISMATCH": 0, "PARTIAL": 0}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    comparable = counts["MATCH"] + counts["MISMATCH"]
    rate = (counts["MATCH"] / comparable) if comparable else None
    return {
        "total": len(rows),
        "match": counts["MATCH"],
        "mismatch": counts["MISMATCH"],
        "partial": counts["PARTIAL"],
        "comparable": comparable,
        "agreement_rate": rate,
    }


# ── Legacy side: run the real analyzer with the INSERT intercepted ───────────


class _CaptureConn:
    """Forwards reads to a real asyncpg connection; captures the hiring_signals
    INSERT instead of executing it (so the legacy formula runs write-free)."""

    def __init__(self, real: Any, sink: list[dict[str, Any]]) -> None:
        self._real = real
        self._sink = sink

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._real.fetch(*args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        return await self._real.fetchrow(*args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return await self._real.fetchval(*args, **kwargs)

    async def execute(self, sql: str, *args: Any) -> str:
        if "hiring_signals" in sql:
            # Arg order from hiring_analyzer.analyze_hiring_trend's INSERT:
            #   stock_id, observed_date, job_count, baseline, relative_strength, is_spike
            self._sink.append(
                {
                    "stock_id": args[0],
                    "job_count": args[2],
                    "baseline": float(args[3]),
                    "relative_strength": float(args[4]),
                    "is_spike": bool(args[5]),
                }
            )
            return "INSERT 0 0"  # no-op: nothing written
        return await self._real.execute(sql, *args)

    async def executemany(self, sql: str, args_iter: Any, *extra: Any) -> Any:
        """배치 INSERT 가로채기 — 레거시 analyze_hiring_trend는 hiring_signals 업서트에
        ``executemany``(배치)를 쓴다. execute 경로와 동일 arg 순서:
        (stock_id, observed_date, job_count, baseline, relative_strength, is_spike[, phase])."""
        if "hiring_signals" in sql:
            for a in args_iter:
                self._sink.append(
                    {
                        "stock_id": a[0],
                        "job_count": a[2],
                        "baseline": float(a[3]),
                        "relative_strength": float(a[4]),
                        "is_spike": bool(a[5]),
                    }
                )
            return None  # no-op: nothing written
        return await self._real.executemany(sql, args_iter, *extra)


class _AcquireCtx:
    def __init__(self, pool: Any, sink: list[dict[str, Any]]) -> None:
        self._pool = pool
        self._sink = sink
        self._ctx: Any = None

    async def __aenter__(self) -> _CaptureConn:
        self._ctx = self._pool.acquire()
        real = await self._ctx.__aenter__()
        return _CaptureConn(real, self._sink)

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._ctx.__aexit__(*exc)


class _CapturePool:
    """Pool wrapper whose acquired connections capture the hiring_signals INSERT."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.sink: list[dict[str, Any]] = []

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self._real, self.sink)


async def run_legacy(pool: Any, as_of: date) -> dict[int, dict[str, Any]]:
    """Return {stock_id: legacy metrics} for ``as_of`` without writing anything.

    The legacy analyzer scores every stock with postings on the date in one call.
    Its class-level baseline cache is reset so a long-lived process re-reads fresh.
    """
    LegacyHiringAnalyzer._is_cache_loaded = False
    LegacyHiringAnalyzer._baseline_cache = {}
    capture = _CapturePool(pool)
    analyzer = LegacyHiringAnalyzer(capture)
    await analyzer.analyze_hiring_trend(as_of.isoformat())
    return {row["stock_id"]: row for row in capture.sink}


# ── New side: run the registry's HIRING loader+analyzer, read-only ───────────


async def run_new_for_stock(
    pool: Any,
    *,
    stock_id: int,
    stock_code: str,
    as_of: date,
) -> dict[str, Any]:
    """SourceResult for one stock from the new HIRING analyzer (no persistence)."""
    from signal_alpha_data_access.repositories import RawDetailRepository

    registration = next(r for r in build_registry() if r.source == "HIRING")
    async with pool.acquire() as conn:
        repository = RawDetailRepository(conn)
        loader = registration.build_loader(repository)
        evidence = await loader.load(stock_id=stock_id, stock_code=stock_code, as_of=as_of)
    result = await registration.analyzer.analyze(stock_code, evidence)
    return {"direction": result.direction, "score": result.score}


# ── Orchestration / reporting ────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    as_of = date.fromisoformat(args.date) if args.date else date.today()

    # 로컬 Docker Postgres(SSL 미지원)에서도 parity가 돌도록 host 기반 SSL 해제(#279).
    # resolve_ssl: 로컬 호스트 → False(SSL off), 그 외 → "require"(prod 불변). DB_SSL override.
    conn_kwargs = parse_dsn(dsn)
    pool = await asyncpg.create_pool(
        **conn_kwargs,
        min_size=1,
        max_size=6,
        ssl=resolve_ssl(conn_kwargs["host"]),
        statement_cache_size=0,
    )
    try:
        async with pool.acquire() as conn:
            targets = await fetch_target_stocks(
                conn,
                ticker=args.ticker,
                wanted={"HIRING"},
                since=None,
                limit=args.limit,
            )
        legacy = await run_legacy(pool, as_of)

        rows: list[dict[str, Any]] = []
        for target in targets:
            stock_id = target["stock_id"]
            stock_code = target["ticker"]
            new = await run_new_for_stock(
                pool, stock_id=stock_id, stock_code=stock_code, as_of=as_of
            )
            leg = legacy.get(stock_id)
            rs = leg["relative_strength"] if leg else None
            spike = bool(leg["is_spike"]) if leg else False
            leg_b = legacy_bucket(rs, spike, down_below=args.down_below, up_at=args.up_at)
            new_b = new_bucket(new["direction"])
            rows.append(
                {
                    "ticker": stock_code,
                    "name": target.get("name"),
                    "legacy_relative_strength": rs,
                    "legacy_is_spike": spike,
                    "legacy_bucket": leg_b,
                    "new_direction": new["direction"],
                    "new_score": round(new["score"], 3),
                    "new_bucket": new_b,
                    "verdict": verdict(leg_b, new_b),
                }
            )
    finally:
        await pool.close()

    rows.sort(key=lambda r: ({"MISMATCH": 0, "PARTIAL": 1, "MATCH": 2}[r["verdict"]], r["ticker"]))
    return {"as_of": as_of.isoformat(), "rows": rows, "summary": summarize(rows)}


def print_report(report: dict[str, Any]) -> None:
    s = report["summary"]
    print("\nHIRING PARITY  (legacy hiring_signals  vs  new AlternativeSignal)")
    print(f"as_of={report['as_of']}  read-only")
    print("=" * 78)
    print(f"{'TICKER':<8}{'LEGACY rs/spike':<20}{'L-BKT':<7}{'NEW dir/score':<22}{'N-BKT':<7}{'VERDICT'}")
    print("-" * 78)
    for r in report["rows"]:
        rs = "-" if r["legacy_relative_strength"] is None else f"{r['legacy_relative_strength']:.0f}%"
        legacy_cell = f"{rs}/{'spike' if r['legacy_is_spike'] else '-'}"
        new_cell = f"{r['new_direction']} {r['new_score']:+.3f}"
        print(
            f"{r['ticker']:<8}{legacy_cell:<20}{r['legacy_bucket']:<7}"
            f"{new_cell:<22}{r['new_bucket']:<7}{r['verdict']}"
        )
    print("-" * 78)
    rate = "n/a" if s["agreement_rate"] is None else f"{s['agreement_rate'] * 100:.1f}%"
    print(
        f"total={s['total']}  match={s['match']}  mismatch={s['mismatch']}  "
        f"partial={s['partial']}  agreement(over comparable)={rate}"
    )
    if s["mismatch"]:
        print("\n⚠️  MISMATCH rows are the cutover risk list — review before retiring legacy.")
    if s["partial"]:
        print("ℹ️  PARTIAL = one side had no data (often: no posting exactly on as_of). Not a conflict.")


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Legacy vs new hiring signal parity check (read-only).")
    parser.add_argument("--date", help="YYYY-MM-DD (default: today).")
    parser.add_argument("--ticker", help="Limit to one ticker.")
    parser.add_argument("--limit", type=int, help="Cap number of stocks.")
    parser.add_argument("--down-below", type=float, default=100.0,
                        help="Legacy relative_strength below this %% = DOWN bucket (default 100).")
    parser.add_argument("--up-at", type=float, default=150.0,
                        help="Legacy relative_strength at/above this %% = UP bucket (default 150 = spike).")
    parser.add_argument("--json", help="Also write the full report as JSON to this path.")
    args = parser.parse_args()

    report = await run(args)
    print_report(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
