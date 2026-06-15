# app/analyzers/hiring_analyzer.py
"""
HiringAnalyzer: 채용 공고 강도 분석기
- 네이버 검색 트렌드의 계절성 가중치 활용
- 최근 14일 공고 수의 이동평균으로 기준선 설정
- Cold Start (Day 0~13) 단계에서 네이버 기반 fallback 적용
"""
import asyncio
from datetime import datetime
from typing import Any

# 도메인 임계값 상수화
HIRING_SPIKE_THRESHOLD = 1.5   # 평년 대비 150% 이상 = 급증
DEFAULT_SEASONAL_FACTOR = 1.0  # 계절 데이터 없을 때 기본값
DEFAULT_BASELINE_SCALE = 1.0   # 기준선 데이터 전무할 때 최소값 (Phase C)

# Cold Start 판정: rolling avg가 이 값 이상이면 신뢰도 높은 실제 데이터로 판정
MIN_ROLLING_AVG_THRESHOLD = 1.0

# 오탐 방지: 오늘 공고 건수가 이 값 미만이면 분석 스킵 (0→소수건 노이즈 차단)
MIN_TODAY_JOB_COUNT = 3

# Phase B 분모 하한: 검색량이 낮은 기업의 base_scale이 0에 가까워 relative_strength가 폭발하는 것 방지
# (예: 검색량 2 → base_scale=0.02 → 1건만 올려도 5000% → 가짜 Spike)
MIN_PHASE_B_EXPECTED = 0.5


class HiringAnalyzer:
    """
    매일 수집된 채용 공고를 네이버 검색 트렌드와 융합하여 분석
    
    데이터 흐름:
    1. bootstrap_hiring_baseline.py (일회성): 과거 3년 네이버 트렌드 → q1~q4_factor, avg_search_volume 저장
    2. HiringCollector (매일): 워크넷 등에서 오늘 공고 수 → hiring_raw_details 저장
    3. HiringAnalyzer (매일): hiring_raw_details + hiring_baseline → 상대 강도 계산 → hiring_signals 저장
    """

    # 클래스 변수: 메모리 캐시 (FastAPI 웹서버/배치 스크립트 모두 대응)
    _baseline_cache: dict[int, dict] = {}
    _is_cache_loaded: bool = False
    # Double-Checked Locking: 서버 최초 기동 시 동시 요청이 몰려도 DB 중복 쿼리 방지
    _cache_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, pool: Any) -> None:
        """
        Args:
            pool: asyncpg 연결 풀
        """
        self._pool = pool

    async def load_baselines(self) -> None:
        """hiring_baseline 테이블 전체를 메모리로 로드 (N+1 방지, 캐시 재사용)."""
        if HiringAnalyzer._is_cache_loaded:
            return

        # Double-Checked Locking: 락 획득 전에 먼저 체크 → 첫 번째 코루틴만 DB 쿼리 실행
        async with HiringAnalyzer._cache_lock:
            if HiringAnalyzer._is_cache_loaded:
                return

            async with self._pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT stock_id,
                           avg_search_volume,
                           q1_factor, q2_factor, q3_factor, q4_factor
                    FROM hiring_baseline
                    WHERE avg_search_volume > 0
                """)

                HiringAnalyzer._baseline_cache = {
                    row["stock_id"]: {
                        "avg_search_volume": float(row["avg_search_volume"]),
                        "Q1": float(row["q1_factor"]),
                        "Q2": float(row["q2_factor"]),
                        "Q3": float(row["q3_factor"]),
                        "Q4": float(row["q4_factor"]),
                    }
                    for row in rows
                }

            HiringAnalyzer._is_cache_loaded = True

    def _get_current_quarter(self, target_date_str) -> str:
        """
        날짜(str / date / datetime)를 Q1~Q4로 변환

        Args:
            target_date_str: "YYYY-MM-DD" str, datetime.date, 또는 datetime 객체
        """
        from datetime import date as date_type
        if isinstance(target_date_str, datetime):
            # datetime 객체 직접 수용 (시분초 포함 경우 대비)
            dt = target_date_str
        elif isinstance(target_date_str, date_type):
            dt = datetime(target_date_str.year, target_date_str.month, target_date_str.day)
        else:
            # 문자열 앞 10자리(YYYY-MM-DD)만 사용 — "2026-06-15 09:30:00" 같은 포맷도 안전하게 처리
            dt = datetime.strptime(str(target_date_str)[:10], "%Y-%m-%d")
        quarter_num = (dt.month - 1) // 3 + 1
        return f"Q{quarter_num}"

    def _get_baseline_scale(self, stock_id: int, rolling_avg: float) -> tuple[float, str]:
        """3단계 Fallback으로 기준선과 Phase 레이블을 함께 반환.

        Returns:
            (base_scale, phase)  phase ∈ {"A", "B", "C"}
        """
        # Phase A: 14일 이동평균 충분 → 실제 데이터 기반
        if rolling_avg >= MIN_ROLLING_AVG_THRESHOLD:
            return rolling_avg, "A"

        # Phase B: Cold Start → 네이버 검색량 기반 fallback
        baseline = HiringAnalyzer._baseline_cache.get(stock_id)
        if baseline and baseline["avg_search_volume"] > 0:
            normalized = baseline["avg_search_volume"] / 100.0
            # MIN_PHASE_B_EXPECTED(0.5) 하한: 검색량이 낮은 기업도 분모가 너무 작아지지 않게 보정
            return max(normalized, MIN_PHASE_B_EXPECTED), "B"

        # Phase C: 데이터 완전 부재 → 최소 기준값
        return DEFAULT_BASELINE_SCALE, "C"

    async def analyze_hiring_trend(self, target_date: str) -> None:
        """
        특정 날짜의 채용 공고 강도를 분석하고 hiring_signals 테이블에 저장
        
        로직:
        1. 최근 14일 일별 공고 수 조회 및 주식별 평균 계산
        2. 오늘 수집된 각 주식별 공고 수 조회
        3. 기준선 = 이동평균 × 분기 가중치 (또는 Cold Start fallback)
        4. 상대 강도 = 오늘 공고 수 / 기준선 × 100
        5. Spike 판정 (≥ 150%) 및 저장
        
        Args:
            target_date: "2024-06-15" 형태의 분석 대상 날짜
            
        Raises:
            asyncpg.exceptions.UndefinedTableError: 테이블 부재
            Exception: DB 연결 오류
        """
        await self.load_baselines()
        current_quarter = self._get_current_quarter(target_date)

        async with self._pool.acquire() as conn:
            # Step 1: 지난 14일간의 총 공고 수 조회 후 14.0으로 나누어 정확한 일평균 계산
            # ⭐ 주의: AVG(daily_count) 함정 제거
            #    데이터베이스는 공고가 0개인 날에는 행을 생성하지 않으므로,
            #    서브쿼리로 AVG()를 사용하면 공고가 있던 날들만 대상으로 평균을 내게 되어
            #    기준선이 수배까지 과대평가됨.
            #    예: 14일 중 2일에만 각 7개 = (7+7)/2 = 7.0 (틀림)
            #        → 정정: 14개 / 14.0 = 1.0 (맞음)
            # Step 1: 지난 14일 총 공고 수 / 14.0 = 일평균
            # hiring_raw_details 는 공고 1건=1행 구조 (job_count 항상 1).
            # COUNT(raw_document_id) 가 총 공고 수와 동일하며, AVG() 보다 정확함.
            # — AVG()는 공고가 없는 날(행 없음)을 분모에서 제외해 기준선을 과대평가하는 함정이 있음.
            scale_rows = await conn.fetch("""
                SELECT stock_id,
                       COUNT(raw_document_id) / 14.0 AS rolling_avg
                FROM hiring_raw_details
                WHERE observed_date BETWEEN $1::DATE - INTERVAL '14 days'
                                        AND $1::DATE - INTERVAL '1 day'
                GROUP BY stock_id
            """, target_date)

            rolling_averages = {
                row["stock_id"]: float(row["rolling_avg"]) 
                for row in scale_rows
            }

            # Step 2: 오늘 수집된 채용 공고 수 조회
            today_rows = await conn.fetch("""
                SELECT stock_id, COUNT(raw_document_id) as today_count
                FROM hiring_raw_details
                WHERE observed_date = $1::DATE
                GROUP BY stock_id
            """, target_date)

            # Step 3: 각 주식별 분석 및 저장
            for row in today_rows:
                stock_id = row["stock_id"]
                today_count = row["today_count"]

                # 소수 건수 노이즈 차단 (0→1건 같은 일상 변동이 Spike로 오판되는 현상 방지)
                if today_count < MIN_TODAY_JOB_COUNT:
                    continue

                # 현재 분기의 계절 가중치 조회 (Phase C면 DEFAULT_SEASONAL_FACTOR=1.0)
                baseline_factors = HiringAnalyzer._baseline_cache.get(stock_id)
                seasonal_factor = (
                    baseline_factors.get(current_quarter, DEFAULT_SEASONAL_FACTOR)
                    if baseline_factors
                    else DEFAULT_SEASONAL_FACTOR
                )

                # 기준선 결정 + Phase 레이블 (이동평균 → 네이버 fallback → 기본값)
                rolling_avg = rolling_averages.get(stock_id, 0)
                base_scale, _phase = self._get_baseline_scale(stock_id, rolling_avg)

                # 기대값 = 기준선 × 계절 가중치 (0 나누기 방어 포함)
                expected_baseline_count = base_scale * seasonal_factor
                if expected_baseline_count <= 0:
                    expected_baseline_count = DEFAULT_BASELINE_SCALE

                # 상대 강도 계산 (%)
                relative_strength = (today_count / expected_baseline_count) * 100

                # Spike 판정 (평년 대비 150% 이상)
                is_spike = relative_strength >= (HIRING_SPIKE_THRESHOLD * 100)

                # Step 4: hiring_signals 테이블에 저장
                await conn.execute("""
                    INSERT INTO hiring_signals
                        (stock_id, observed_date, job_count, baseline,
                         relative_strength, is_spike)
                    VALUES
                        ($1, $2::DATE, $3, $4, $5, $6)
                    ON CONFLICT (stock_id, observed_date) DO UPDATE
                    SET job_count         = EXCLUDED.job_count,
                        baseline          = EXCLUDED.baseline,
                        relative_strength = EXCLUDED.relative_strength,
                        is_spike          = EXCLUDED.is_spike
                """, stock_id, target_date, today_count,
                     round(expected_baseline_count, 2),
                     round(relative_strength, 2), is_spike)


# ============================================================================
# 사용 예시
# ============================================================================
"""
# FastAPI 라우터에서 사용 (추천)
from fastapi import FastAPI

app = FastAPI()
pool = None  # asyncpg.Pool 인스턴스 (startup에서 초기화)

@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool("postgresql://...")
    
    # HiringAnalyzer 싱글톤 초기화
    analyzer = HiringAnalyzer(pool)
    await analyzer.load_baselines()

@app.post("/analyze/hiring/{date}")
async def trigger_analysis(date: str):
    analyzer = HiringAnalyzer(pool)
    await analyzer.analyze_hiring_trend(date)
    return {"status": "done"}

---

# 배치 스크립트에서 사용 (크론잡)
import asyncio
import asyncpg

async def daily_batch():
    pool = await asyncpg.create_pool("postgresql://...")
    analyzer = HiringAnalyzer(pool)
    
    today = datetime.now().strftime("%Y-%m-%d")
    await analyzer.analyze_hiring_trend(today)
    
    await pool.close()

if __name__ == "__main__":
    asyncio.run(daily_batch())
"""