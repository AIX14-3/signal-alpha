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
  (extra_payload에 rolling_avg_14d /
   avg_search_volume / seasonal_factor 포함)
          ↓
  ┌───────────────────────────────────────────────┐
  │  분석기 두 가지 — 역할이 다름                  │
  │                                               │
  │  hiring_analyzer.py  (배치 엔진, 매일)        │
  │  └─ 14일 이동평균 + 계절 가중치 → 기대값      │
  │  └─ relative_strength / is_spike 계산         │
  │  └─ hiring_signals UPSERT (executemany)       │
  │                                               │
  │  analyzer.py  (Protocol 준수, 실시간)         │
  │  └─ evidence.metadata에서 기준선 값 추출       │
  │  └─ SourceResult 반환 (DB 쿼리 없음)          │
  └───────────────────────────────────────────────┘
          ↓
  hiring_signals (is_spike + calculation_phase 포함)
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
psql $DATABASE_URL -f database/migrations/017_hiring_signals_add_calculation_phase.sql
```

| 파일 | 변경 내용 |
|------|----------|
| `006` *(기존)* | `hiring_baseline` — main에 이미 포함 (`006_collection_hiring.sql`) |
| `002` *(기존)* | `stocks.is_target`, `short_name` — main에 이미 포함 (`002_market.sql`) |
| `seeds/001` *(기존)* | 15개 핵심 기업 seed — main에 이미 포함 (`seeds/001_seed_stocks.sql`) |
| **`014`** | `hiring_raw_details`에 `observed_date DATE` 추가 |
| **`015`** | `hiring_signals` — 분석 결과 저장 |
| **`016`** | `hiring_sources` — 기업별 크롤러 설정 (ticker 기반 SSoT) |
| **`017`** | `hiring_signals.calculation_phase VARCHAR(1)` 추가 — A/B/C 계산 근거 추적 |

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
job_count, baseline, relative_strength, is_spike,
calculation_phase  ← 'A'|'B'|'C' (분석 근거 추적용)
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
| `base_collector.py` | DB 적재 공통 로직. Savepoint 격리, SHA-256 중복 방지, `extra_payload`에 `rolling_avg_14d` / `avg_search_volume` / `seasonal_factor` 저장 |
| `keyword_generator.py` | 기업명 → 네이버 DataLab 키워드 그룹 변환 (Pure 로직, DB 호출 없음) |
| `multi_source_crawler.py` | 5계층 소스 통합 오케스트레이터 (포털 + 공식 사이트) |
| `driver_utils.py` | Chrome WebDriver 팩토리 (Anti-Bot 옵션 + WebDriver Manager) |
| `main.py` | 대화형 CLI 메뉴 (수동 실행용) |
| `sites/` | 사이트별 크롤러 구현체 (Saramin, Jobkorea, Samsung 등) |

#### `base_collector.py` — `extra_payload` 메타데이터 구조

수집 완료 후 해당 기업의 기준선 데이터를 함께 저장하여 `analyzer.py`가 DB 없이 순수 연산 가능하도록 합니다.

```json
{
  "rolling_avg_14d": 5.2,
  "avg_search_volume": 60.0,
  "seasonal_factor": 1.1
}
```

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

**분석기가 두 개입니다. 역할이 다르므로 import 경로에 주의하세요.**

| 파일 | import 경로 | 역할 |
|------|------------|------|
| `analyzer.py` | `from app.analyzers.hiring import HiringAnalyzer` | **Analyzer Protocol 준수** — `analyze(stock_code, evidence) → SourceResult`. DB 쿼리 없이 `evidence.metadata`에서 기준선 추출 후 순수 연산. 오케스트레이터·Aggregator 연동용. |
| `hiring_analyzer.py` | `from app.analyzers.hiring.hiring_analyzer import HiringAnalyzer` | **배치 엔진** — `analyze_hiring_trend(target_date)`. asyncpg로 DB 직접 쿼리, `hiring_signals` UPSERT. 일별 크론잡 전용. |

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
# 의존성 설치 (Windows: tzdata 패키지 포함)
uv sync

# Chrome + ChromeDriver (WebDriver Manager가 자동 설치)
# Chrome 120+ 권장
```

### 1. 최초 초기화 (한 번만)

```bash
# DB 마이그레이션 적용 (014 → 017 순서대로)
psql $DATABASE_URL -f database/migrations/014_hiring_raw_details_observed_date.sql
psql $DATABASE_URL -f database/migrations/015_hiring_signals.sql
psql $DATABASE_URL -f database/migrations/016_hiring_sources.sql
psql $DATABASE_URL -f database/migrations/017_hiring_signals_add_calculation_phase.sql

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
Step 1: MultiSourceCrawler.run()
  → hiring_raw_details 적재 (Savepoint 격리)
  → extra_payload에 rolling_avg_14d / avg_search_volume / seasonal_factor 함께 저장
  ↓ (3초 대기)
Step 2: HiringAnalyzer.analyze_hiring_trend()
  → executemany 배치 업서트 → hiring_signals (calculation_phase 포함)
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

# FastAPI 장기 가동 환경에서 분기 갱신 후 캐시를 비워야 할 때
# (코드 내 또는 관리자 API 라우터에서 호출)
await HiringAnalyzer.clear_cache()
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

### 배치 엔진 (`hiring_analyzer.py`) 실행 흐름

`HiringAnalyzer.analyze_hiring_trend(target_date)` 호출 시:

**Step 1.** 지난 14일 이동평균 계산
```sql
-- hiring_raw_details는 공고 1건=1행 구조 (job_count 항상 1)
-- AVG()는 공고 0건인 날(행 없음)을 분모에서 제외해 기준선 과대평가 → COUNT/14.0 사용
SELECT stock_id, COUNT(raw_document_id) / 14.0 AS rolling_avg
FROM hiring_raw_details
WHERE observed_date BETWEEN :date - 14 DAYS AND :date - 1 DAY
GROUP BY stock_id
```

**Step 2.** 3단계 Fallback으로 기준선 결정 — `_get_baseline_scale(stock_id, rolling_avg) → (scale, phase)`

| Phase | 조건 | 기준선 | 신뢰도 |
|-------|------|--------|--------|
| **A** — Day 14+ | `rolling_avg ≥ 1.0` | 14일 이동평균 | 높음 |
| **B** — Cold Start | `rolling_avg < 1.0`, DataLab 데이터 있음 | `max(avg_search_volume / 100, 0.5)` | 중간 |
| **C** — 데이터 없음 | DataLab 데이터도 없음 | `1.0` (최솟값) | 낮음 |

> **Phase B 하한선 (`MIN_PHASE_B_EXPECTED = 0.5`):**  
> 검색량이 낮은 소형주의 `base_scale`이 0.01 수준으로 떨어지면 공고 1건에 상대강도 5,000%가 나오는 분모 폭발이 발생함. 최솟값 0.5로 통제.

> **소수 건수 필터 (`MIN_TODAY_JOB_COUNT = 3`):**  
> 0→1건 같은 일상 변동이 Spike로 오판되는 현상 방지. 오늘 공고가 3건 미만이면 분석 스킵.

**Step 3.** 상대 강도 계산 및 Spike 판정
```python
expected = base_scale * seasonal_factor
if expected <= 0:
    expected = DEFAULT_BASELINE_SCALE  # 0 나누기 방어
relative_strength = (today_count / expected) * 100
is_spike = relative_strength >= 150   # 평년 대비 150% 이상
```

**Step 4.** `hiring_signals` 배치 업서트 (단 1회 네트워크 왕복)
```sql
-- executemany로 전체 기업을 한 번에 처리 (루프 내 개별 execute 대비 수십 배 빠름)
INSERT INTO hiring_signals
    (stock_id, observed_date, job_count, baseline,
     relative_strength, is_spike, calculation_phase)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (stock_id, observed_date) DO UPDATE SET ...
```

### Protocol 분석기 (`analyzer.py`) 실행 흐름

오케스트레이터가 `list[RawEvidence]`를 전달하면 DB 쿼리 없이 `SourceResult`를 반환합니다.

```python
result = await analyzer.analyze(stock_code="005930", evidence=[...])
# result.direction: "positive" | "neutral" | "negative"
# result.score: 0.0 ~ 100.0
# result.summary: "채용 8건 (14일 평균 대비 +60.0%, Phase A), 주요 기술: ..."
# result.data_status: "ok" | "low_confidence" | "insufficient_data" | "failed"
```

**Phase별 summary 문구:**

| Phase | summary 내 기준선 표현 |
|-------|----------------------|
| A | `"14일 평균 대비 +X.X%"` |
| B | `"트렌드 기준 대비 +X.X%"` |
| C | `"기본 기준선 대비 +X.X%"` |

> LLM 상위 레이어가 "Phase B인데 14일 평균 대비?" 혼란을 겪지 않도록 동적으로 생성.

**`_PHASE_B_MIN_EXPECTED = 0.5` 적용:**  
검색량 극소 기업도 `change_pct` 최대 약 ±500% 수준으로 통제. score는 ±50%p 클램핑으로 0~100점 보장됨.

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

# 전체 채용 관련 테스트 (39케이스)
uv run pytest tests/ -k "hiring" -v

# HiringAnalyzer 단위 테스트 (23케이스, asyncpg 불필요)
uv run pytest tests/analyzers/test_hiring_analyzer.py -v

# HiringKeywordGenerator 단위 테스트 (16케이스)
uv run pytest tests/test_hiring_keyword_generator.py -v
```

**`test_hiring_analyzer.py` 커버 범위 (23케이스):**

| 클래스 | 검증 내용 |
|--------|----------|
| `TestGetCurrentQuarter` (4) | 날짜 str / date / datetime → Q1~Q4 경계값 |
| `TestGetBaselineScale` (7) | Phase A / B / C 전환 조건, `tuple[float, str]` 반환, Phase B 클램핑 |
| `TestSpikeDetection` (5) | 150% 경계값, seasonal_factor 보정, expected≤0 방어 |
| `TestLoadBaselines` (2) | 캐시 최초 로드 / 재사용 방지 (asyncio.Lock 포함) |
| `TestAnalyzeHiringTrend` (5) | executemany 배치 업서트, spike 판정, Phase C INSERT, Cold Start fallback |

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
Phase B 동작 여부는 `hiring_signals.calculation_phase = 'B'` 로 확인 가능합니다.

### 캐시 갱신 (FastAPI 장기 가동 환경)

분기가 바뀌거나 신규 주식이 추가되어도 프로세스 재시작 전까지 메모리 캐시가 갱신되지 않습니다:
```python
# 관리자 API 라우터 또는 분기 전환 감지 로직에서 호출
await HiringAnalyzer.clear_cache()
# 다음 analyze_hiring_trend 호출 시 DB에서 최신 hiring_baseline 재로드
```

### Windows 한글 깨짐 (대화형 실행)

`main.py`는 Windows cp949 콘솔 환경에서 UTF-8 강제 설정이 적용되어 있습니다.  
그래도 깨지면 PowerShell에서 실행 전:
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### 파이프라인 타임아웃

`run_daily_hiring_pipeline.py`의 기본 타임아웃은 단계당 3600초(1시간).  
15개 기업 × 멀티소스 크롤링은 통상 300~600초 소요.  
완료 후 `logs/hiring_pipeline.log`에서 단계별 소요 시간을 확인하세요.
