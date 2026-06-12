"""
채용 분석 데모 스크립트
수집된 hiring_raw_details 데이터를 HiringAnalyzer로 분석하고 결과를 콘솔에 출력합니다.
"""
import asyncio
import os
import sys
from datetime import date, datetime

import asyncpg
from dotenv import load_dotenv

# .env 로드 (프로젝트 루트 기준)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../../.env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.analyzers.hiring.hiring_analyzer import HiringAnalyzer


async def run_demo(target_date: date | None = None):
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL 환경변수가 없습니다.")
        return

    pool = await asyncpg.create_pool(db_url)

    # 분석할 날짜 결정 (미지정 시 가장 최근 수집일 사용)
    if target_date is None:
        async with pool.acquire() as conn:
            latest = await conn.fetchval(
                "SELECT MAX(observed_date) FROM hiring_raw_details"
            )
        if latest is None:
            print("ERROR: hiring_raw_details에 데이터가 없습니다. 먼저 collector를 실행하세요.")
            await pool.close()
            return
        target_date = latest

    print(f"\n{'='*60}")
    print(f"  [HIRING ANALYZER DEMO]  target: {target_date}")
    print(f"{'='*60}")

    analyzer = HiringAnalyzer(pool)
    await analyzer.load_baselines()

    # 기준선 상태 출력
    baseline_count = len(HiringAnalyzer._baseline_cache)
    if baseline_count == 0:
        print("\n[기준선] hiring_baseline 데이터 없음 → Phase C (DEFAULT=1.0) 적용")
        print("         bootstrap_hiring_baseline.py 실행 후 Phase A/B가 활성화됩니다.")
    else:
        print(f"\n[기준선] {baseline_count}개 기업의 네이버 DataLab 기준선 로드됨")

    print()

    # 분석 실행
    await analyzer.analyze_hiring_trend(target_date)

    # 결과 조회 및 출력
    async with pool.acquire() as conn:
        results = await conn.fetch("""
            SELECT
                s.ticker,
                COALESCE(s.short_name, s.name) AS company,
                hs.job_count,
                hs.baseline,
                hs.relative_strength,
                hs.is_spike
            FROM hiring_signals hs
            JOIN stocks s ON s.id = hs.stock_id
            WHERE hs.observed_date = $1
            ORDER BY hs.relative_strength DESC NULLS LAST
        """, target_date)

    if not results:
        print("분석 결과가 없습니다.")
        await pool.close()
        return

    # 헤더
    print(f"{'티커':<8} {'기업':<14} {'공고수':>6} {'기준선':>8} {'상대강도(%)':>12} {'Spike':>6}")
    print("-" * 60)

    spike_companies = []
    for r in results:
        rs = r["relative_strength"]
        rs_str = f"{rs:.1f}%" if rs is not None else "N/A"
        baseline_str = f"{r['baseline']:.2f}" if r["baseline"] is not None else "N/A"
        spike_mark = "🔴 SPIKE" if r["is_spike"] else ""
        if r["is_spike"]:
            spike_companies.append(r["company"])
        print(
            f"{r['ticker']:<8} {r['company']:<14} {r['job_count']:>6}"
            f" {baseline_str:>8} {rs_str:>12} {spike_mark}"
        )

    print("-" * 60)

    # 요약
    total = len(results)
    spike_count = sum(1 for r in results if r["is_spike"])
    print(f"\n[요약] 분석 {total}개 기업, Spike 감지 {spike_count}개")
    if spike_companies:
        print(f"       Spike 기업: {', '.join(spike_companies)}")

    print(f"\n[Phase 설명]")
    print(f"  Phase A: 14일 이동평균 ≥ 1.0   → 실제 데이터 기반 (신뢰 높음)")
    print(f"  Phase B: 14일 이동평균 < 1.0    → DataLab 검색량 기반 (Cold Start)")
    print(f"  Phase C: baseline 없음          → 기본값 1.0 적용 (신뢰 낮음)")
    print(f"  Spike 기준: 상대강도 ≥ 150%")

    await pool.close()
    print(f"\n결과는 hiring_signals 테이블에도 저장됩니다.")
    print(f"  SELECT * FROM hiring_signals WHERE observed_date = '{target_date}';\n")


if __name__ == "__main__":
    target = None
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])
    asyncio.run(run_demo(target))
