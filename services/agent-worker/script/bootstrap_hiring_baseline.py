# scripts/bootstrap_hiring_baseline.py
import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# script/ 단독 실행 시 app 패키지 import 가능하게 + repo 루트 .env 로드
# (sibling script/dashboard_validator.py와 동일 컨벤션)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT.parent.parent / ".env")  # repo root .env → DATABASE_URL/HIRING_DATALAB_*

from app.clients.naver_datalab_client import NaverDataLabClient  # noqa: E402
from app.collectors.hiring.keyword_generator import HiringKeywordGenerator  # noqa: E402

async def bootstrap_baselines(pool, datalab_client: NaverDataLabClient):
    generator = HiringKeywordGenerator()

    # 1. DB에서 수집 대상 기업 리스트 조회 (stocks.name 컬럼 사용)
    async with pool.acquire() as conn:
        stocks = await conn.fetch("SELECT id, name, short_name FROM stocks WHERE is_target = TRUE")
    
    # 3년 전 날짜 계산
    start_date_obj = date.today() - timedelta(days=365 * 3)
    end_date_obj = date.today()
    
    start_date_str = start_date_obj.isoformat()
    end_date_str = end_date_obj.isoformat()

    for stock in stocks:
        stock_id = stock["id"]
        c_name = stock["name"]
        s_name = stock["short_name"]
        
        # 2. Generator로 네이버 API 규격 키워드 그룹 변환
        kw_group = generator.generate_keyword_group(company_name=c_name, short_name=s_name)
        group_name = kw_group["groupName"]
        
        try:
            # 3. NaverDataLabClient 호출 (3년치 트렌드를 위해 주간 단위 'week' 사용)
            records = await datalab_client.search_grouped(
                keyword_group=group_name,
                keywords=kw_group["keywords"],
                start_date=start_date_str,
                end_date=end_date_str,
                time_unit="week"
            )
            
            if not records:
                print(f"⚠️ {c_name}의 검색 기록 데이터가 없습니다. 건너뜁니다.")
                continue
                
            # 4. 분기별 가중치 및 평균 계산 파싱 로직
            total_ratio = 0.0
            quarter_ratios = {"Q1": [], "Q2": [], "Q3": [], "Q4": []}
            
            for r in records:
                total_ratio += r.ratio
                # r.period 형식: "2024-03-15"에서 월 추출
                month = int(r.period.split("-")[1])
                q_label = f"Q{(month - 1) // 3 + 1}"
                quarter_ratios[q_label].append(r.ratio)
            
            avg_search_volume = total_ratio / len(records)
            
            # 분기별 factor 계산 (분기평균 / 전체평균)
            factors = {}
            for q, values in quarter_ratios.items():
                q_avg = sum(values) / len(values) if values else avg_search_volume
                factors[q] = round(q_avg / avg_search_volume, 3) if avg_search_volume > 0 else 1.000

            # 5. 실제 DB 스키마(hiring_baseline) 컬럼명에 맞춰 매핑 및 UPSERT
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO hiring_baseline (
                        stock_id, avg_search_volume, 
                        q1_factor, q2_factor, q3_factor, q4_factor, 
                        keyword_group_name, data_start_date, data_end_date, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::DATE, $9::DATE, NOW())
                    ON CONFLICT (stock_id) DO UPDATE 
                    SET avg_search_volume = EXCLUDED.avg_search_volume,
                        q1_factor = EXCLUDED.q1_factor,
                        q2_factor = EXCLUDED.q2_factor,
                        q3_factor = EXCLUDED.q3_factor,
                        q4_factor = EXCLUDED.q4_factor,
                        keyword_group_name = EXCLUDED.keyword_group_name,
                        data_start_date = EXCLUDED.data_start_date,
                        data_end_date = EXCLUDED.data_end_date,
                        updated_at = NOW()
                """, 
                stock_id, avg_search_volume,
                factors["Q1"], factors["Q2"], factors["Q3"], factors["Q4"],
                group_name, start_date_obj, end_date_obj)

                # 6. 원시 주간 시계열 적재(#291) — analyzer는 집계값만 쓰지만,
                #    트렌드 시각화/감사용으로 버려지던 records를 그대로 저장한다.
                #    len==10 가드: 깨진 period로 인한 date.fromisoformat ValueError 방어.
                period_type = NaverDataLabClient.time_unit_to_period_type("week")
                trend_rows = [
                    (stock_id, group_name, date.fromisoformat(r.period), r.ratio, period_type)
                    for r in records
                    if r.period and len(r.period) == 10
                ]
                if trend_rows:
                    await conn.executemany("""
                        INSERT INTO hiring_search_trend (
                            stock_id, keyword_group, period_date, search_index, period_type
                        )
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT ON CONSTRAINT uq_hiring_search_trend_stock_date
                        DO UPDATE
                        SET search_index = EXCLUDED.search_index,
                            keyword_group = EXCLUDED.keyword_group,
                            period_type = EXCLUDED.period_type
                    """, trend_rows)
                    print(f"   └ {c_name} 트렌드 {len(trend_rows)}주 적재 완료")

            print(f"✅ {c_name} (ID: {stock_id}) Baseline 적재 완료!")
            await asyncio.sleep(0.5)  # API 과부하 방지 디레이
            
        except Exception as e:
            print(f"❌ {c_name} Baseline 생성 중 에러 발생: {e}")


async def main() -> None:
    """최초 1회 또는 분기 1회 실행하는 DataLab 기준선 부트스트랩 진입점."""
    database_url = os.environ["DATABASE_URL"]
    client_id    = os.environ["HIRING_DATALAB_CLIENT_ID"]
    client_secret = os.environ["HIRING_DATALAB_CLIENT_SECRET"]

    pool = await asyncpg.create_pool(database_url)
    datalab_client = NaverDataLabClient(
        client_id=client_id,
        client_secret=client_secret,
    )

    try:
        await bootstrap_baselines(pool, datalab_client)
        print("✅ 전체 부트스트랩 완료")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
