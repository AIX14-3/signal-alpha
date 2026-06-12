# agent-worker — 채용 신호 수집·분석 서비스

채용공고 수집 → 계절성 기준선 비교 → 이상 급등(Spike) 신호 생성까지의 전체 파이프라인.

---

## 목차

1. [아키텍처](#아키텍처)
2. [DB 스키마 및 마이그레이션](#db-스키마-및-마이그레이션)
3. [주요 컴포넌트](#주요-컴포넌트)
4. [환경 변수](#환경-변수)
5. [실행 방법](#실행-방법)
6. [채용 분석 로직 — 3단계 Fallback](#채용-분석-로직--3단계-fallback)
7. [Zero-Hardcoding 설계](#zero-hardcoding-설계)
8. [테스트](#테스트)
9. [트러블슈팅](#트러블슈팅)

---

## 아키텍처

```
외부 데이터 소스
├─ 채용 포털 (사람인, 잡코리아)
├─ 기업 공식 사이트 (Samsung, NAVER, Kakao 등 15개)
└─ 네이버 DataLab API (검색 트렌드 — 기준선 부트스트랩 전용)

          ↓ 수집 (매일)               ↓ 기준선 초기화 (분기 1회)
  MultiSourceCrawler            bootstrap_hiring_baseline.py
          ↓                               ↓
  hiring_raw_details            hiring_baseline (q1~q4 가중치)
          ↓
  HiringAnalyzer (매일)
  ├─ 14일 이동평균 + 계절 가중치 → 기대값
  └─ (오늘 공고 수 / 기대값) × 100 → relative_strength
          ↓
  hiring_signals (is_spike 포함)
```

**3계층 수집 저장:**
```
collector_runs          ← 실행 로그 (시작/종료/통계)
  └─ raw_documents      ← 공고 원본 메타 (UNIQUE: source_type + external_id)
       └─ hiring_raw_details  ← 공고 상세 + JSONB extra_payload
```

---

## DB 스키마 및 마이그레이션

마이그레이션은 **번호 순서대로** 적용합니다. (001~013은 main 브랜치에 이미 적용된 기반 스키마)

```bash
psql $DATABASE_URL -f database/migrations/014_hiring_raw_details_observed_date.sql
psql $DATABASE_URL -f database/migrations/015_hiring_signals.sql
psql $DATABASE_URL -f database/migrations/016_hiring_sources.sql
```

| 파일 | 변경 내용 |
|------|----------|
| `006` *(기존)* | `hiring_baseline` — main에 이미 포함 (`006_collection_hiring.sql`) |
| `002` *(기존)* | `stocks.is_target`, `short_name` — main에 이미 포함 (`002_market.sql`) |
| `seeds/001` *(기존)* | 15개 핵심 기업 seed — main에 이미 포함 (`seeds/001_seed_stocks.sql`) |
| **`014`** | `hiring_raw_details`에 `observed_date DATE` 추가 |
| **`015`** | `hiring_signals` — 분석 결과 저장 |
| **`016`** | `hiring_sources` — 기업별 크롤러 설정 (ticker 기반 SSoT) |

### 핵심 테이블 요약

**`hiring_baseline`** — 네이버 DataLab 3년 트렌드 기반 기준선 (분기 1회 UPSERT)
```
stock_id (UNIQUE FK), avg_search_volume,
q1_factor, q2_factor, q3_factor, q4_factor,
keyword_group_name, data_start_date, data_end_date
```

**`hiring_signals`** — HiringAnalyzer 일별 분석 결과
```
stock_id + observed_date (UNIQUE),
job_count, baseline, relative_strength, is_spike
```

**`hiring_sources`** — 기업별 공식 사이트 크롤러 설정
```
stock_id + crawler_type (UNIQUE),
crawler_class, base_url, extra_config, is_active
```
> 새 기업 추가 = `hiring_sources` INSERT 1줄. 코드 재배포 불필요.

---

## 주요 컴포넌트

### 수집기 (`app/collectors/hiring/`)

| 파일 | 역할 |
|------|------|
| `base_collector.py` | DB 적재 공통 로직 (Savepoint 격리, SHA-256 중복 방지, 계절 기준선 참조) |
| `keyword_generator.py` | 기업명 → 네이버 DataLab 키워드 그룹 변환 (Pure 로직, DB 호출 없음) |
| `multi_source_crawler.py` | 5계층 소스 통합 오케스트레이터 (포털 + 공식 사이트) |
| `driver_utils.py` | Chrome WebDriver 팩토리 (Anti-Bot 옵션 + WebDriver Manager) |
| `main.py` | 대화형 CLI 메뉴 (수동 실행용) |
| `sites/` | 사이트별 크롤러 구현체 (Saramin, Jobkorea, Samsung 등) |

#### 수집 소스 5계층

| 유형 | 대상 기업 | 방식 |
|------|----------|------|
| 포털 검색 | 전체 15개 | Saramin + Jobkorea 키워드 검색 |
| `official_api` | 삼성전자 | HTTP requests (driver=None) |
| `official_selenium` | NAVER, 카카오, SK하이닉스, 크래프톤, HYBE, SM, 현대, 기아 | Selenium SPA/ATS |
| `recruiter_kr` | HL만도, 셀트리온, 유한양행 | recruiter.co.kr |
| `simple_site` | 한미반도체, 스튜디오드래곤, 삼성바이오로직스 | requests + Selenium fallback |

#### 드라이버 로테이션
헤드리스 Chrome은 45+ 페이지 이동 후 메모리 1~2 GB 누적 → 크래시.  
기본값 `driver_rotation_size=3`으로 **3개 기업마다 Chrome 재시작**.

### 분석기 (`app/analyzers/hiring/`)

| 파일 | 역할 |
|------|------|
| `hiring_analyzer.py` | 채용 강도 분석 엔진 (asyncpg 기반, 3단계 Fallback 기준선) |

### 스크립트 (`script/`)

| 파일 | 실행 주기 | 역할 |
|------|----------|------|
| `bootstrap_hiring_baseline.py` | 최초 1회 + 분기 1회 | 네이버 DataLab 3년치 트렌드 → `hiring_baseline` UPSERT |
| `run_daily_hiring_pipeline.py` | 매일 (cron / Airflow) | Step 1 수집 → Step 2 분석 자동화 |

---

## 환경 변수

`.env.example` 참조. 채용 파이프라인 필수 항목:

```dotenv
# PostgreSQL 연결
DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha

# 네이버 DataLab — 부트스트랩 전용 (별도 앱 등록 필수, DataLab 파트와 쿼터 분리)
HIRING_DATALAB_CLIENT_ID=
HIRING_DATALAB_CLIENT_SECRET=
```

> **주의:** `HIRING_DATALAB_*`은 `NAVER_DATALAB_*`과 **다른 앱**입니다.  
> 쿼터를 공유하면 429 에러가 발생하므로 반드시 분리 등록하세요.

---

## 실행 방법

### 사전 조건

```bash
# 의존성 설치
uv sync

# Chrome + ChromeDriver (WebDriver Manager가 자동 설치)
# Chrome 120+ 권장
```

### 1. 최초 초기화 (한 번만)

```bash
# DB 마이그레이션 적용 (014 → 023 순서대로)
psql $DATABASE_URL -f database/migrations/014_hiring_baseline.sql
# ... (순서대로 023까지)

# 네이버 DataLab 기준선 부트스트랩 (3년치 트렌드 수집, ~5분 소요)
cd services/agent-worker
uv run python script/bootstrap_hiring_baseline.py
```

### 2. 일일 파이프라인 실행

```bash
cd services/agent-worker

# 오늘 날짜 자동 처리
uv run python script/run_daily_hiring_pipeline.py

# 특정 날짜 지정
uv run python script/run_daily_hiring_pipeline.py --date 2026-06-12
```

**파이프라인 흐름:**
```
Step 1: MultiSourceCrawler.run()     ← 재시도 최대 3회, 타임아웃 3600초
  → hiring_raw_details 적재 (Savepoint 격리)
  ↓ (3초 대기)
Step 2: HiringAnalyzer.analyze_hiring_trend()  ← 재시도 최대 3회
  → hiring_signals UPSERT
```

로그: `logs/hiring_pipeline.log`

### 3. 대화형 수동 실행 (`main.py`)

```bash
cd services/agent-worker
uv run python app/collectors/hiring/main.py
```

```
메뉴:
  1. Mock Collector       — 픽스처 기반 테스트 (DB 연결 필요)
  2. Web Crawler          — 사람인 + 잡코리아만
  3. Multi-Source Crawler — 전체 소스 (포털 + 공식 사이트)
  4. [Admin] DataLab 키워드 그룹 미리보기
  0. 종료
```

### 4. 기준선 재구축 (분기 1회 권장)

```bash
uv run python script/bootstrap_hiring_baseline.py
```

### 5. Airflow DAG 연동

```python
from airflow.operators.python import PythonOperator

def run_pipeline_task(**context):
    import asyncio
    from script.run_daily_hiring_pipeline import run_full_pipeline
    success = asyncio.run(run_full_pipeline(context['ds']))
    if not success:
        raise Exception("채용 파이프라인 실패")

task = PythonOperator(
    task_id='hiring_pipeline',
    python_callable=run_pipeline_task,
    provide_context=True,
)
```

---

## 채용 분석 로직 — 3단계 Fallback

`HiringAnalyzer.analyze_hiring_trend(target_date)` 실행 흐름:

**Step 1.** 지난 14일 이동평균 계산
```sql
-- ⚠️ AVG() 대신 COUNT/14.0 사용: 공고 0건인 날은 행이 없으므로
--    AVG()는 "공고 있던 날"만 평균 → 기준선 과대평가
SELECT stock_id, COUNT(id) / 14.0 AS rolling_avg
FROM hiring_raw_details
WHERE observed_date BETWEEN :date - 14 AND :date - 1
  AND job_count > 0
GROUP BY stock_id
```

**Step 2.** 3단계 Fallback으로 기준선(Scale) 결정

| Phase | 조건 | 기준선 | 신뢰도 |
|-------|------|--------|--------|
| **A** — Day 14+ | `rolling_avg ≥ 1.0` | 14일 이동평균 | 높음 |
| **B** — Cold Start | `rolling_avg < 1.0` | `avg_search_volume / 100` | 중간 |
| **C** — 데이터 없음 | 네이버 기준선도 없음 | `1.0` (최솟값) | 낮음 |

**Step 3.** 상대 강도 계산 및 Spike 판정
```python
expected = base_scale * seasonal_factor   # seasonal_factor = q{N}_factor
relative_strength = (today_count / expected) * 100
is_spike = relative_strength >= 150       # 평년 대비 150% 이상
```

**Step 4.** `hiring_signals` UPSERT
```sql
INSERT INTO hiring_signals (stock_id, observed_date, job_count, baseline, relative_strength, is_spike)
ON CONFLICT (stock_id, observed_date) DO UPDATE SET ...
```

---

## Zero-Hardcoding 설계

기업 정보는 모두 DB가 Single Source of Truth입니다. 코드 수정 없이 SQL만으로 관리:

| 변경 작업 | SQL |
|----------|-----|
| 새 기업 추가 | `stocks` INSERT + `hiring_sources` INSERT |
| 수집 대상 제외 | `UPDATE stocks SET is_target = FALSE WHERE ticker = '...'` |
| 약칭 변경 | `UPDATE stocks SET short_name = '...' WHERE ticker = '...'` |
| 공식 크롤러 교체 | `UPDATE hiring_sources SET crawler_class = '...' WHERE stock_id = ...` |

**`hiring_sources.crawler_type` ENUM:**
```
official_api       — HTTP requests (driver=None)
official_selenium  — Selenium SPA/ATS
recruiter_kr       — recruiter.co.kr 집계
simple_site        — 정적/CMS 사이트
portal_saramin     — (별도 처리, 이 테이블 불필요)
portal_jobkorea    — (별도 처리, 이 테이블 불필요)
```

> **현재 제한:** `base_url` 컬럼은 메타데이터 목적이며 실제 크롤러 URL은 각 `.py` 파일 내 상수(`_BASE`)를 사용합니다.

---

## 테스트

```bash
cd services/agent-worker

# 전체 채용 관련 테스트
uv run pytest tests/ -k "hiring" -v

# HiringAnalyzer 단위 테스트 (20개 케이스, asyncpg 불필요)
uv run pytest tests/analyzers/test_hiring_analyzer.py -v

# HiringKeywordGenerator 단위 테스트 (17개 케이스)
uv run pytest tests/test_hiring_keyword_generator.py -v
```

**`test_hiring_analyzer.py` 커버 범위:**

| 클래스 | 검증 내용 |
|--------|----------|
| `TestGetCurrentQuarter` | 날짜 → Q1~Q4 경계값 |
| `TestGetBaselineScale` | Phase A / B / C 전환 조건 |
| `TestSpikeDetection` | 150% 경계값, seasonal_factor 보정 |
| `TestLoadBaselines` | 캐시 최초 로드 / 재사용 방지 |
| `TestAnalyzeHiringTrend` | INSERT 검증, spike 판정, Cold Start fallback |

> 비동기 테스트는 `unittest.IsolatedAsyncioTestCase` 사용 (`pytest-asyncio` 불필요).

---

## 트러블슈팅

### Chrome WebDriver 오류

```
SessionNotCreatedException: Chrome version mismatch
```
→ WebDriver Manager가 자동 다운로드하지만, 오프라인 환경에서는 수동 설치 필요:
```bash
pip install webdriver-manager
python -c "from webdriver_manager.chrome import ChromeDriverManager; ChromeDriverManager().install()"
```

### 네이버 DataLab 429 Too Many Requests

→ `HIRING_DATALAB_*`과 `NAVER_DATALAB_*`이 동일 앱을 사용 중인지 확인.  
네이버 개발자 센터에서 `Signal-Alpha-Hiring` 앱을 **별도 등록**해야 합니다.

### 기업 등록 오류 (`_SkipRecord`)

```
WARNING: stocks 매칭 실패 — '주식회사 ABC' 스킵
```
→ `stocks` 테이블에 기업이 없거나 `is_target=FALSE` 상태:
```sql
-- 현재 수집 대상 확인
SELECT ticker, name, is_target, short_name FROM stocks WHERE is_target = TRUE;

-- 새 기업 활성화
UPDATE stocks SET is_target = TRUE WHERE ticker = '000000';
```

### Cold Start (14일 데이터 부족)

처음 2주간은 Phase B(네이버 검색량 기반)로 동작하며 신뢰도가 낮습니다.  
`bootstrap_hiring_baseline.py`를 실행했는지 확인하세요.

### 파이프라인 타임아웃

`run_daily_hiring_pipeline.py`의 기본 타임아웃은 단계당 3600초(1시간).  
15개 기업 × 멀티소스 크롤링은 통상 300~600초 소요.  
완료 후 `logs/hiring_pipeline.log`에서 단계별 소요 시간을 확인하세요.

### Windows 한글 깨짐 (대화형 실행)

`main.py`는 Windows cp949 콘솔 환경에서 UTF-8 강제 설정이 적용되어 있습니다.  
그래도 깨지면 PowerShell에서 실행 전:
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```
