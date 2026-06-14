"""Entry point: ingest -> backtest -> (optional) LLM -> report.

    python run.py --ingest            # backfill 10y daily candles to parquet
    python run.py --backtest          # run walk-forward + write REPORT.md
    python run.py --backtest --llm    # also run the LLM judge layer
    python run.py --all               # ingest + backtest (+llm if keys present)

Run with the harness venv:
    ../.venv/Scripts/python.exe run.py --backtest
"""

from __future__ import annotations

import argparse
import asyncio
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from config import REPO_ROOT, HERE


def _load_env() -> None:
    if load_dotenv:
        load_dotenv(REPO_ROOT / ".env")
        load_dotenv(HERE / ".env", override=True)


async def _amain(args) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    _load_env()

    if args.ingest or args.all:
        from ingest import ingest_all
        print("=== INGEST (Toss 10y daily candles) ===")
        counts = await ingest_all()
        ok = sum(1 for v in counts.values() if v)
        print(f"적재 완료: {ok}/{len(counts)} 종목")

    if args.backtest or args.all:
        from backtest import run_backtest
        import charts
        from report import build_report

        print("=== BACKTEST (walk-forward OOS) ===")
        results = run_backtest()

        llm = None
        if args.llm:
            from llm_judge import run_llm_judge
            print("=== LLM JUDGE ===")
            llm = await run_llm_judge()
            if llm is None:
                print("  (LLM 키 미설정 → 스킵)")

        chart_files = charts.make_all(results)
        build_report(results, chart_files, llm)
        from config import REPORT_PATH
        print(f"리포트 작성됨 → {REPORT_PATH}")

    if not (args.ingest or args.backtest or args.all):
        print("아무 작업도 선택되지 않음. --ingest / --backtest / --all 중 하나를 지정하세요.")
        return 2
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="AI-tech technical-analysis backtest")
    p.add_argument("--ingest", action="store_true", help="backfill candles to parquet")
    p.add_argument("--backtest", action="store_true", help="run walk-forward + report")
    p.add_argument("--llm", action="store_true", help="include LLM judge layer")
    p.add_argument("--all", action="store_true", help="ingest + backtest")
    return asyncio.run(_amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
