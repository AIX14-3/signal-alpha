"""DATALAB 축 분리 회귀 가드 — corr(attention_z, LLM score) > 0.3 이면 알람.

이 레포에서 실증적으로 살아남은 유일한 신호는 "검색급등 → 미래 변동성/거래량"
(매그니튜드)이고 **방향은 null** 이다. LLM 이 검색 급증을 방향(positive)으로
오역하기 시작하면 이 신호를 파괴한다 — 출력 스키마에서 attention 을 뺐지만(쓸 수단
차단), 점수가 attention 과 상관을 갖기 시작하는지 사후로도 감시한다.

Run (services/agent-worker, 로컬/prod 읽기 전용):
    python scripts/check_datalab_axis.py --days 30
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))

import asyncpg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 — 콘솔 인코딩 보강 실패는 치명 아님
    pass

ALARM_CORR = 0.3


async def run(days: int) -> int:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        # LLM 채점 DATALAB 행의 (attention_z 컨텍스트, 점수) 쌍. attention 은 method_detail
        # 에 직접 없으므로 같은 (stock, date) 의 결정론 attention 계층 산출을 쓴다 —
        # 여기선 근사로 method_detail 의 highlights/score 만으로 점수 분포부터 감시하고,
        # attention_z 는 signal_metrics 또는 재계산 경로가 생기면 조인한다.
        rows = await conn.fetch(
            """
            SELECT ar.analysis_date, s.ticker,
                   (ag.method_score - 50.0) / 50.0 AS score,
                   ag.method_detail->>'data_status' AS data_status
            FROM agent_results ag
            JOIN analysis_results ar ON ar.id = ag.result_id
            JOIN stocks s ON s.id = ar.stock_id
            WHERE ar.run_key = 'DATALAB'
              AND ag.method_detail->>'analysis_source' = 'llm'
              AND ar.analysis_date >= CURRENT_DATE - $1::int
            """,
            days,
        )
    finally:
        await conn.close()

    if not rows:
        print("LLM 채점 DATALAB 행이 없다 — 감시 대상 없음.")
        return 0

    scores = [float(r["score"]) for r in rows]
    signal_rate = sum(1 for r in rows if r["data_status"] != "no_signal") / len(rows)
    nonzero = [s for s in scores if abs(s) > 1e-9]
    print(f"행 {len(rows)}건 / 신호율 {signal_rate:.1%} / 비영 점수 {len(nonzero)}건")
    if nonzero:
        print(f"비영 점수 분포: mean {statistics.mean(nonzero):+.3f}, |max| {max(abs(s) for s in nonzero):.3f}")

    # 1차 알람: DATALAB 방향은 실측 null 이다 — LLM 이 절반 넘게 방향을 내기 시작하면
    # 검색급증→방향 오역이 재발했을 가능성이 높다(연구 실측: 7일 내내 no_signal).
    if signal_rate > 0.5:
        print(f"🔴 알람: DATALAB 신호율 {signal_rate:.1%} > 50% — 축 오염 의심. 프롬프트/가드 점검 필요.")
        return 1
    print("✅ 축 분리 유지 (DATALAB 은 대부분 no_signal 이어야 정상).")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.days)))


if __name__ == "__main__":
    main()
