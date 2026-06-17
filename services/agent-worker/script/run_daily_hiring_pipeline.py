# scripts/run_daily_hiring_pipeline.py
"""
매일 밤 자동 실행되는 채용 데이터 수집 및 분석 파이프라인

실행 순서:
1. HiringCollector: 워크넷 등에서 오늘 채용공고 수집 및 적재
2. HiringAnalyzer: 최근 14일 이동평균(0분포 보정) + 네이버 트렌드 가중치 기반 신호 생성
"""

import asyncio
import asyncpg
import logging
import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional
from pathlib import Path

_KST = ZoneInfo("Asia/Seoul")  # 분석 대상일 '오늘' 경계를 KST로 고정(observed_date와 일치, #253)

from dotenv import load_dotenv

# 프로젝트 루트 경로 자동 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# repo root(.env)를 로드해 DATABASE_URL을 단일 소스로 사용 가능하게 한다.
# PROJECT_ROOT = services/agent-worker → repo root = .parent.parent
load_dotenv(PROJECT_ROOT.parent.parent / ".env")

# 1️⃣ [버그 수정] logs 디렉터리가 없으면 자동 생성하여 FileHandler 크래시 방지
Path("logs").mkdir(exist_ok=True)

from app.collectors.hiring.multi_source_crawler import MultiSourceCrawler
from app.analyzers.hiring.hiring_analyzer import HiringAnalyzer

# ============================================================================
# 로깅 설정
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler("logs/hiring_pipeline.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# 파이프라인 설정 환경 변수화
# ============================================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "signal_alpha")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "your_password")  # 실무에선 .env로 관리

# DATABASE_URL(.env 표준)이 있으면 우선 사용 — run_collectors.py / base_collector 와 일관.
# 미설정 시에만 쪼개진 DB_* 변수로 폴백(레거시 호환).
DSN = os.getenv("DATABASE_URL") or f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

RETRY_COUNT = 3
RETRY_BACKOFF = 5        # 실패 시 재시도 대기 시간(초)
WAIT_BETWEEN_STAGES = 3  # 3️⃣ [최적화] 동기적 커밋이 보장되므로 300초 대기를 3초로 단축
TIMEOUT_PER_STAGE = 3600 # 단계별 타임아웃 (1시간)


# ============================================================================
# 실행 코어 로직 (재시도 로직 루프 구조로 개선)
# ============================================================================
async def run_hiring_collector(pool: asyncpg.Pool, target_date: str) -> bool:
    """Step 1: 채용공고 데이터 수집기 실행 (최대 3회 재시도)"""
    for retry in range(RETRY_COUNT + 1):
        try:
            logger.info(f"🔵 [Step 1] MultiSourceCrawler 시작 (날짜: {target_date}, 시도: {retry+1}/{RETRY_COUNT+1})")
            crawler = MultiSourceCrawler(database_url=DSN, headless=True)

            start_time = datetime.now()
            collected_count = await asyncio.wait_for(
                asyncio.to_thread(crawler.run),
                timeout=TIMEOUT_PER_STAGE
            )
            elapsed = (datetime.now() - start_time).total_seconds()

            logger.info(f"✅ [Step 1] MultiSourceCrawler 완료 - {collected_count}개 수집 완료 ({elapsed:.1f}초)")
            return True
            
        except (asyncio.TimeoutError, Exception) as e:
            logger.error(f"❌ [Step 1] 시도 {retry+1} 실패: {type(e).__name__} - {str(e)}")
            if retry < RETRY_COUNT:
                await asyncio.sleep(RETRY_BACKOFF)
    return False


async def run_hiring_analyzer(pool: asyncpg.Pool, target_date: str) -> bool:
    """Step 2: 네이버 검색량 융합 채용 강도 분석기 실행 (최대 3회 재시도)"""
    for retry in range(RETRY_COUNT + 1):
        try:
            logger.info(f"🔵 [Step 2] HiringAnalyzer 시작 (날짜: {target_date}, 시도: {retry+1}/{RETRY_COUNT+1})")
            analyzer = HiringAnalyzer(pool)
            
            start_time = datetime.now()
            await asyncio.wait_for(
                analyzer.analyze_hiring_trend(target_date),
                timeout=TIMEOUT_PER_STAGE
            )
            elapsed = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"✅ [Step 2] HiringAnalyzer 분석 및 시그널 적재 완료 ({elapsed:.1f}초)")
            return True
            
        except (asyncio.TimeoutError, Exception) as e:
            logger.error(f"❌ [Step 2] 시도 {retry+1} 실패: {type(e).__name__} - {str(e)}")
            if retry < RETRY_COUNT:
                await asyncio.sleep(RETRY_BACKOFF)
    return False


async def run_full_pipeline(target_date: Optional[str] = None) -> bool:
    """전체 파이프라인 오케스트레이션 총괄"""
    if target_date is None:
        target_date = datetime.now(_KST).strftime("%Y-%m-%d")  # KST 기준(#253)
    
    logger.info("=" * 80)
    logger.info(f"🚀 채용 데이터 엔지니어링 파이프라인 가동 ({target_date})")
    logger.info("=" * 80)
    
    pool = None
    try:
        logger.info("🔄 데이터베이스 커넥션 풀 초기화 중...")
        pool = await asyncpg.create_pool(DSN, min_size=2, max_size=10, timeout=30)
        
        # 1단계: 수집
        if not await run_hiring_collector(pool, target_date):
            logger.error("🚨 [Critical] Step 1 파이프라인 최종 실패. 프로세스를 강제 종료합니다.")
            return False
            
        # 단계 사이의 안전 대기
        await asyncio.sleep(WAIT_BETWEEN_STAGES)
        
        # 2단계: 분석
        if not await run_hiring_analyzer(pool, target_date):
            logger.error("🚨 [Critical] Step 2 파이프라인 최종 실패. 분석 데이터 유실 위험.")
            return False
            
        logger.info("=" * 80)
        logger.info(f"🎉 오늘의 모든 채용 신호 파이프라인 정상 종료 ({target_date})")
        logger.info("=" * 80)
        return True
        
    except Exception as e:
        logger.error(f"💥 파이프라인 치명적 시스템 에러: {str(e)}")
        return False
    finally:
        if pool:
            await pool.close()
            logger.info("🔒 DB 커넥션 풀 안전하게 반환 완료")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Signal Alpha 채용 파이프라인 스크립트")
    parser.add_argument("--date", type=str, default=None, help='분석 대상 날짜 (YYYY-MM-DD)')
    args = parser.parse_args()
    
    success = await run_full_pipeline(args.date)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================================
# 2️⃣ [안전성 강화] 올바른 Airflow DAG 정의 가이드 (참고용)
# ============================================================================
"""
# Airflow Task 정의 시 비동기 루프 실행 방식 필수 적용:

def run_pipeline_task(**context):
    import asyncio
    from scripts.run_daily_hiring_pipeline import run_full_pipeline
    
    target_date = context['ds']  # Airflow 배치 실행 날짜 추출 (YYYY-MM-DD)
    
    # 비동기 함수를 동기식 동력원 위에서 안전하게 실행하도록 래핑
    success = asyncio.run(run_full_pipeline(target_date))
    if not success:
        raise Exception("Airflow Task 구동 중 파이프라인 내부 결함으로 실패 처리")

task = PythonOperator(
    task_id='hiring_pipeline_task',
    python_callable=run_pipeline_task,
    provide_context=True,
    dag=dag,
)
"""