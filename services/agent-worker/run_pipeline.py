"""ML/DL/LLM 라인 E2E 러너 — 합성 시드 OHLCV → 결과값(RiskReport / 배치 CSV).

worker-redesign 의 게이트형 체인을 **단일 프로세스에서 큐를 드레인**해 끝까지 돌린다. 무거운
수집기/임베딩 핸들러는 import 하지 않고 MVP 경로(ML_INFER·META_COMBINE·AGGREGATE·SYNTHESIZE·
RISK_VETO)만 등록한다.

  # 단일 종목: ML 추론 → 결합 → (final_signal 있으면) LLM 종합 → RiskReport JSON
  uv run python run_pipeline.py --ticker 005930 --out data/report_005930.json

  # 배치: 여러 종목 모델별 pred_vol + 결합치 CSV(베이스라인 비교표)
  uv run python run_pipeline.py --tickers 005930,000660,035720 --csv data/mvp_baseline.csv

ML/DL 라인 결과(ml_inferences·meta_signals)는 합성 OHLCV만으로 산출된다. LLM 라인(SYNTHESIZE)은
final_signal(소스 분석 산출)이 있어야 돌므로, 종목에 final_signal 이 있을 때만 종합을 시도한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()

# bootstrap() 가 sys.path 에 워크스페이스 패키지를 얹은 뒤라야 app.* 를 import 할 수 있다(E402 의도됨).
from app.core.config import get_settings  # noqa: E402
from app.orchestrator.queue.task_types import (  # noqa: E402
    AGGREGATE_SIGNAL,
    META_COMBINE,
    ML_INFER,
    RISK_VETO,
    SYNTHESIZE,
)
from app.ml.model_registry import resolve_models  # noqa: E402
from app.orchestrator.queue.tasks import QueueTaskRunner  # noqa: E402
from app.synthesis.tasks import synthesis_llm_enabled  # noqa: E402

# MVP 드레인 대상 — worker-redesign 의 ML→결합→게이트2→종합→veto 선형 체인.
MVP_TASK_TYPES = (ML_INFER, META_COMBINE, AGGREGATE_SIGNAL, SYNTHESIZE, RISK_VETO)


@lru_cache(maxsize=1)
def _model_order() -> tuple[str, ...]:
    """배치/리포트의 모델 컬럼 순서를 '실제로 돌 모델'에서 파생한다 — 백테스트 게이트
    (``ML_GATE_PASSED_MODELS``) ∩ 가용성 게이트(``HAVE_*``). 하드코딩 상수가 게이트와
    어긋나 모델이 누락되거나(예: tcn 추가 후 누락) 미설치 모델이 죽은 컬럼으로 잔존
    (예: degated lightgbm)하는 것을 원천 차단. ``lru_cache`` 로 프로세스 내 모든
    호출이 동일 순서를 보장(컬럼·행·DB preds 정합)."""
    return tuple(spec.name for spec in resolve_models())


def _build_handlers(conn: Any, settings: Any) -> dict[str, Any]:
    """MVP 경로 핸들러만 — build_task_handlers 의 무거운 수집기/임베딩 import 회피."""
    from app.gates.risk_veto import RiskVetoTaskHandler
    from app.ml.inference import MlInferTaskHandler
    from app.ml.meta_combine import MetaCombineTaskHandler
    from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
    from app.synthesis.tasks import SynthesizeTaskHandler

    return {
        ML_INFER: MlInferTaskHandler(conn),
        META_COMBINE: MetaCombineTaskHandler(conn),
        AGGREGATE_SIGNAL: AggregateSignalTaskHandler(conn),
        SYNTHESIZE: SynthesizeTaskHandler(conn, settings=settings),
        RISK_VETO: RiskVetoTaskHandler(conn, settings=settings),
    }


async def _drain(pool: Any, settings: Any) -> list[dict[str, Any]]:
    """큐가 완전히 idle 될 때까지 MVP task_type 들을 순회 드레인. 각 task 결과를 모아 반환."""
    collected: list[dict[str, Any]] = []
    while True:
        progressed = False
        for task_type in MVP_TASK_TYPES:
            async with pool.acquire() as conn:
                runner = QueueTaskRunner(conn, _build_handlers(conn, settings))
                result = await runner.run_task(task_type)
            if result["status"] == "idle":
                continue
            progressed = True
            collected.append(result)
            if result["status"] in {"failed", "skipped"}:
                print(f"  ⚠️  {task_type} task#{result.get('task_id')}: "
                      f"{result['status']} {result.get('error', '')}")
        if not progressed:
            break
    return collected


async def _reset_targets(conn: Any, stock_ids: list[int]) -> None:
    """멱등 재실행용 — 대상 종목의 ML/meta/큐 상태를 비운다(ohlcv·final_signals 는 보존)."""
    await conn.execute("DELETE FROM ml_inferences WHERE stock_id = ANY($1::bigint[])", stock_ids)
    await conn.execute("DELETE FROM meta_signals WHERE stock_id = ANY($1::bigint[])", stock_ids)
    # dead_letter 가 처리 큐를 FK 참조하므로 큐 삭제 전에 먼저 비운다(이전 실패 아카이브 정리).
    await conn.execute(
        "DELETE FROM dead_letter WHERE stock_id = ANY($1::bigint[]) "
        "AND task_type = ANY($2::text[])",
        stock_ids,
        list(MVP_TASK_TYPES),
    )
    await conn.execute(
        "DELETE FROM processing_queue WHERE stock_id = ANY($1::bigint[]) "
        "AND task_type = ANY($2::text[])",
        stock_ids,
        list(MVP_TASK_TYPES),
    )


async def _enqueue_ml(conn: Any, *, stock_id: int, stock_code: str) -> None:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    await ProcessingQueueRepository(conn).enqueue(
        stock_id=stock_id,
        task_type=ML_INFER,
        priority="batch",
        task_context={"stock_code": stock_code, "priority": "batch"},
        dedupe=True,
    )


async def _latest_final_signal(conn: Any, stock_id: int) -> dict[str, Any] | None:
    """종목의 최신 final_signal + 근거 signal_event_ids(LLM 종합 입력)."""
    row = await conn.fetchrow(
        """
        SELECT fs.id AS final_signal_id, fs.run_key, fs.signal_date,
               ar.source_signal_event_ids
        FROM final_signals fs
        JOIN analysis_results ar ON ar.id = fs.analysis_result_id
        WHERE fs.stock_id = $1
        ORDER BY fs.signal_date DESC, fs.id DESC
        LIMIT 1
        """,
        stock_id,
    )
    return dict(row) if row else None


async def _enqueue_synthesize(
    conn: Any, *, stock_id: int, stock_code: str, final_signal: dict[str, Any]
) -> None:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    event_ids = list(final_signal.get("source_signal_event_ids") or [])
    await ProcessingQueueRepository(conn).enqueue(
        stock_id=stock_id,
        task_type=SYNTHESIZE,
        priority="batch",
        source_signal_event_ids=event_ids or None,
        task_context={
            "final_signal_id": int(final_signal["final_signal_id"]),
            "stock_code": stock_code,
            "run_key": "ML",  # 종합이 ML run_key 의 meta_signal 을 ml_risk 로 읽게.
        },
        dedupe=True,
    )


async def _read_ml_result(conn: Any, stock_id: int) -> dict[str, Any]:
    """종목의 최신 asof 모델별 pred_vol + 결합 meta_signal 을 읽어 한 행으로."""
    inf_rows = await conn.fetch(
        """
        SELECT model_name, pred_value, error_message, asof_date, horizon
        FROM ml_inferences
        WHERE stock_id = $1 AND run_key = 'ML'
          AND asof_date = (SELECT max(asof_date) FROM ml_inferences WHERE stock_id = $1 AND run_key = 'ML')
        ORDER BY model_name
        """,
        stock_id,
    )
    meta = await conn.fetchrow(
        """
        SELECT combined_vol, confidence, method, model_count, weight_breakdown, asof_date, horizon
        FROM meta_signals
        WHERE stock_id = $1 AND run_key = 'ML'
        ORDER BY asof_date DESC, horizon ASC LIMIT 1
        """,
        stock_id,
    )
    preds = {r["model_name"]: r for r in inf_rows}
    return {
        "asof_date": str(meta["asof_date"]) if meta else None,
        "horizon": int(meta["horizon"]) if meta else None,
        "models": {
            name: {
                "pred_vol": _f(preds[name]["pred_value"]) if name in preds else None,
                "error": preds[name]["error_message"] if name in preds else "not_run",
            }
            for name in _model_order()
        },
        "combined_vol": _f(meta["combined_vol"]) if meta else None,
        "confidence": _f(meta["confidence"]) if meta else None,
        "method": meta["method"] if meta else None,
        "model_count": int(meta["model_count"]) if meta else 0,
        "weight_breakdown": _loads(meta["weight_breakdown"]) if meta else {},
    }


async def run_single(args: argparse.Namespace) -> None:
    load_env()
    settings = get_settings()
    pool = await build_pool(max_pool_size=4)
    try:
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=[args.ticker], limit=None)
            if not targets:
                raise SystemExit(f"Unknown ticker: {args.ticker}")
            target = targets[0]
            stock_id = int(target["stock_id"])
            if args.reset:
                await _reset_targets(conn, [stock_id])
            await _enqueue_ml(conn, stock_id=stock_id, stock_code=target["ticker"])

        llm_on = synthesis_llm_enabled(settings)
        print("PIPELINE (single)")
        print("=" * 64)
        print(f"{target['ticker']} {target['name']}  stock_id={stock_id}  "
              f"LLM={'on' if (llm_on and not args.no_llm) else 'off (deterministic)'}")

        await _drain(pool, settings)

        synth_report: dict[str, Any] | None = None
        if not args.no_llm:
            async with pool.acquire() as conn:
                final_signal = await _latest_final_signal(conn, stock_id)
                if final_signal:
                    await _enqueue_synthesize(
                        conn, stock_id=stock_id, stock_code=target["ticker"],
                        final_signal=final_signal,
                    )
            if final_signal:
                for result in await _drain(pool, settings):
                    if result["task_type"] == SYNTHESIZE and result["status"] == "success":
                        synth_report = result["result"].get("report")

        async with pool.acquire() as conn:
            ml = await _read_ml_result(conn, stock_id)

        report = {
            "ticker": target["ticker"],
            "name": target["name"],
            "stock_id": stock_id,
            "ml_dl_line": ml,
            "llm_line": synth_report or {"skipped": "no final_signal for stock (run a source analyzer first)"},
        }
        text = json.dumps(report, ensure_ascii=False, indent=2)
        _print_single_summary(report)
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            print(f"\n📄 RiskReport JSON → {out_path}")
        else:
            print("\n" + text)
    finally:
        await pool.close()


async def run_batch(args: argparse.Namespace) -> None:
    load_env()
    settings = get_settings()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    pool = await build_pool(max_pool_size=6)
    try:
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=None)
            found = {t["ticker"] for t in targets}
            missing = [t for t in tickers if t not in found]
            if missing:
                print(f"⚠️  unknown tickers (skipped): {', '.join(missing)}")
            if not targets:
                raise SystemExit("No target stocks resolved.")
            stock_ids = [int(t["stock_id"]) for t in targets]
            if args.reset:
                await _reset_targets(conn, stock_ids)
            for target in targets:
                await _enqueue_ml(conn, stock_id=int(target["stock_id"]), stock_code=target["ticker"])

        print("PIPELINE (batch)")
        print("=" * 64)
        print(f"targets={len(targets)} models={list(_model_order())}")
        await _drain(pool, settings)

        rows: list[dict[str, Any]] = []
        async with pool.acquire() as conn:
            for target in targets:
                ml = await _read_ml_result(conn, int(target["stock_id"]))
                row = {
                    "ticker": target["ticker"],
                    "name": target["name"],
                    "asof_date": ml["asof_date"],
                    "horizon": ml["horizon"],
                    **{m: ml["models"][m]["pred_vol"] for m in _model_order()},
                    "combined_vol": ml["combined_vol"],
                    "confidence": ml["confidence"],
                    "method": ml["method"],
                    "model_count": ml["model_count"],
                }
                rows.append(row)

        _print_batch_table(rows)
        csv_path = Path(args.csv) if args.csv else Path("data") / f"mvp_baseline_{rows[0]['asof_date']}.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["ticker", "name", "asof_date", "horizon", *_model_order(),
                      "combined_vol", "confidence", "method", "model_count"]
        with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        ok = sum(1 for r in rows if r["combined_vol"] is not None)
        print(f"\n📊 baseline {ok}/{len(rows)} stocks combined  →  {csv_path}")
    finally:
        await pool.close()


def _print_single_summary(report: dict[str, Any]) -> None:
    ml = report["ml_dl_line"]
    print(f"\nML/DL line (asof={ml['asof_date']} h={ml['horizon']}):")
    for name in _model_order():
        cell = ml["models"][name]
        val = f"{cell['pred_vol']:.4f}" if cell["pred_vol"] is not None else f"FAIL({cell['error']})"
        print(f"  {name:<9} pred_vol={val}")
    print(f"  combined_vol={ml['combined_vol']} confidence={ml['confidence']} "
          f"method={ml['method']} ({ml['model_count']} models)")
    llm = report["llm_line"]
    if "skipped" in llm:
        print(f"\nLLM line: skipped — {llm['skipped']}")
    else:
        narrative = llm.get("narrative") or {}
        print(f"\nLLM line (source={narrative.get('source')}): {narrative.get('headline')}")
        if llm.get("ml_risk"):
            print(f"  ml_risk: combined_vol={llm['ml_risk'].get('combined_vol')} "
                  f"confidence={llm['ml_risk'].get('confidence')}")


def _print_batch_table(rows: list[dict[str, Any]]) -> None:
    header = f"{'ticker':<8}{'  '.join(f'{m:>9}' for m in _model_order())}{'combined':>11}{'conf':>7}{'method':>16}"
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        cells = "  ".join(
            f"{r[m]:>9.4f}" if r[m] is not None else f"{'—':>9}" for m in _model_order()
        )
        comb = f"{r['combined_vol']:>11.4f}" if r["combined_vol"] is not None else f"{'—':>11}"
        conf = f"{r['confidence']:>7.2f}" if r["confidence"] is not None else f"{'—':>7}"
        print(f"{r['ticker']:<8}{cells}{comb}{conf}{str(r['method']):>16}")


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _loads(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ML/DL/LLM line E2E on seeded stocks.")
    parser.add_argument("--ticker", help="Single-stock E2E → RiskReport JSON.")
    parser.add_argument("--tickers", help="Comma-separated → batch CSV baseline.")
    parser.add_argument("--out", help="Single mode: write RiskReport JSON to this path.")
    parser.add_argument("--csv", help="Batch mode: CSV path (default data/mvp_baseline_<asof>.csv).")
    parser.add_argument("--no-llm", action="store_true", help="Skip the SYNTHESIZE (LLM) stage.")
    parser.add_argument("--reset", action="store_true", help="Clear prior ML/meta/queue rows for targets first.")
    args = parser.parse_args()

    if args.tickers:
        asyncio.run(run_batch(args))
    elif args.ticker:
        asyncio.run(run_single(args))
    else:
        parser.error("Provide --ticker (single) or --tickers (batch).")


if __name__ == "__main__":
    main()
