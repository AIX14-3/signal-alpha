# agent-worker — 채용 신호 수집·분석 서비스

채용공고 수집 → 계절성 기준선 비교 → 이상 급등(Spike) 신호 생성까지의 전체 파이프라인.

> 이 문서는 현행 코드(`services/agent-worker`) 기준으로 검증되어 작성되었습니다.
> 구조 ERD는 `database/erd/signal_alpha_core_erd.md`, 라이브 적재현황은 `script/db_explorer.py`(Streamlit dev 도구) 참조.
>
> **문서 역할 경계:** 본 문서는 수집→분석→`hiring_signals` 의 **운영·구현 매뉴얼**이다.
> 레거시(`hiring_analyzer`→`hiring_signals`) vs 신규(`analyzer`→`final_signals`) 중 무엇을 남길지의
> **컷오버 기준(parity C0~C5)·`final_signals` 네이밍 결정**은 별도 문서
> [hiring-cutover-and-final-signals-naming.md](hiring-cutover-and-final-signals-naming.md) 가 단독 관리한다.

---

## 목차

1. [아키텍처](#아키텍처)
2. [DB 스키마 및 마이그레이션](#db-스키마-및-마이그레이션)
3. [주요 컴포넌트](#주요-컴포넌트)
4. [수집 방어 레이어](#수집-방어-레이어)
5. [환경 변수](#환경-변수)
6. [실행 방법](#실행-방법)
7. [채용 분석 로직 — 3단계 Fallback](#채용-분석-로직--3단계-fallback)
8. [Zero-Hardcoding 설계](#zero-hardcoding-설계)
9. [테스트](#테스트)
10. [트러블슈팅](#트러블슈팅)

---

## 아키텍처

```
외부 데이터 소스
├─ 채용 포털 (사람인, 잡코리아)        — 키워드 검색 (전체 is_target 기업)
├─ 기업 공식 사이트 (15개)             — official_api / official_selenium / recruiter_kr / simple_site
└─ 네이버 DataLab API                  — 검색 트렌드 (기준선 부트스트랩 전용)

          ↓ 수집 (매일)                          ↓ 기준선 초기화 (분기 1회)
  MultiSourceCrawler.run()                bootstrap_hiring_baseline.py
   ├─ collect(): 전 소스 크롤              └─ 3년치 주간 트렌드 → hiring_baseline
   │   ├─ anti-block 레이어 (http.py)         (avg_search_volume, q1~q4_factor)
   │   ├─ 🚧 차단 신호 센서 (403/429 집계)
   │   └─ 🧹 수집단계 선거부 (#176)
   │        미등록 기업을 parse 이전에 드랍
   ├─ parse(): 표준 포맷
   └─ insert_to_db(): 3계층 적재 + 검증 게이트 + stocks 매칭
          ↓
  ┌───────────────────────────────────────────────┐
  │  분석기 두 가지 — 역할이 다름                  │
  │                                               │
  │  hiring_analyzer.py  (배치 엔진, 매일)        │
  │  └─ 14일 이동평균 + 계절 가중치 → 기대값      │
  │  └─ relative_strength / is_spike / phase 계산 │
  │  └─ hiring_signals UPSERT (executemany)       │
  │                                               │
  │  analyzer.py  (Protocol 준수, 오케스트레이터) │
  │  └─ 수집 rows → SourceResult (DB 쿼리 없음)   │
  └───────────────────────────────────────────────┘
          ↓
  hiring_signals (is_spike + calculation_phase 포함)
```

**3계층 수집 저장:**
```
collector_runs          ← 실행 로그 (collected/inserted/skipped/failed)
  └─ raw_documents      ← 공고 원본 메타 (UNIQUE: source_type + external_id, SHA-256 source_hash)
       └─ hiring_raw_details  ← 공고 상세 + JSONB extra_payload
```
> 파싱 실패 원본은 `hiring_quarantine`로 격리되어 selector 수정 후 replay-reparse 가능(#161).

---

## DB 스키마 및 마이그레이션

마이그레이션은 **`database/migrate.py` 러너**로 적용합니다. `psql -f`로 개별 파일을 직접 실행하지 않습니다.

```bash
# 적용 상태 확인
python database/migrate.py status

# 미적용 마이그레이션 적용 (+ 시드)
python database/migrate.py apply --seeds

# 드라이런 / DB 지정
python database/migrate.py apply --dry-run
python database/migrate.py apply --database-url postgresql://user:pw@host:5432/db
```

**원장(`schema_migrations`)** — `filename`(PK) · `checksum`(CHAR(64)) · `applied_at`:
- `database/migrations/*.sql`을 **파일명 순서**로 적용하고 원장에 기록.
- 적용된 파일은 **절대 수정 금지**(checksum 검증으로 차단). 변경은 항상 **새 `NNN_*.sql`** 추가.
- 파일당 한 트랜잭션(마이그레이션 SQL + 원장 INSERT 함께 커밋).
- `seeds/*.sql`은 원장 미기록 — `ON CONFLICT` 기반 idempotent 재실행.
- `DATABASE_URL` 결정 순서: `--database-url` > 환경변수 > 루트 `.env`.

**hiring 관련 스키마 위치** — 통합 베이스라인 `001_baseline.sql`에 hiring 핵심 테이블
(`hiring_baseline`/`hiring_signals`/`hiring_sources`/`hiring_raw_details`)이 포함되고,
격리는 `013_hiring_quarantine.sql`로 추가됨. (과거 문서의 `014~017_hiring_*.sql`은 현재 존재하지 않습니다.)

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
calculation_phase  ← 'A'|'B'|'C' (분석 근거 추적)
```

**`hiring_sources`** — 기업별 공식 사이트 크롤러 설정 (stock_id + crawler_type UNIQUE)
```
crawler_class, crawler_type, base_url, extra_config, is_active
```
> 새 기업 추가 = `hiring_sources` INSERT 1줄. 코드 재배포 불필요.

**`hiring_raw_details.extra_payload`** (JSONB) — 공고 상세 + 수집 시점 계산된 계절 기준선:
```json
{
  "job_title": "...", "job_description": "...", "tech_stack": [],
  "closing_date": "...", "story": "...", "signal_strength": null,
  "source_type": "MULTI_SOURCE_WEB", "unique_key": "...",
  "quarter": "Q2",
  "seasonal_baseline": 18.6   // = hiring_baseline.avg_search_volume × 해당 분기 q_factor
}
```
> `seasonal_baseline`은 적재 시 `base_collector._insert_one`이 `hiring_baseline`을 중첩 savepoint로
> 조회해 계산(`avg_search_volume × q{분기}_factor`). baseline 미존재 시 `null`. (과거 문서의
> `rolling_avg_14d`/`seasonal_factor` 별도 키는 현재 구조가 아님 — rolling avg는 분석기가 런타임 계산.)

---

## 주요 컴포넌트

### 수집기 (`app/collectors/hiring/`)

| 파일 | 역할 |
|------|------|
| `base_collector.py` | DB 적재 공통 로직. Savepoint 격리, SHA-256 중복 방지, stocks 매칭(`_resolve_stock`/공유 `_match_stock_row`), `extra_payload`에 `seasonal_baseline` 계산·저장, 수집단계 선거부용 `_filter_registered` |
| `multi_source_crawler.py` | 멀티소스 오케스트레이터. 드라이버 로테이션, 수집단계 선거부(`_reject_unregistered`), 차단 신호 센서 요약 |
| `keyword_generator.py` | 기업명 → 네이버 DataLab 키워드 그룹 (Pure 로직, DB 호출 없음) |
| `sites/http.py` | requests 공용 fetch — retry+백오프+UA로테이션+429/403 적응형 + 차단 신호 카운터 |
| `sites/` | 사이트별 크롤러 (Saramin, Jobkorea, Samsung, Naver 등) |
| `driver_utils.py` | Chrome WebDriver 팩토리 (Anti-Bot 옵션 + WebDriver Manager) |
| `main.py` | 대화형 CLI 메뉴 (수동 실행용) |

#### 수집 소스 계층 (hiring_sources 실측 기준)

| `crawler_type` | 크롤러 클래스 | 대상 기업 | 방식 |
|------|------|----------|------|
| 포털 검색 | Saramin·Jobkorea | 전체 is_target | 키워드 검색 (별도, hiring_sources 불필요) |
| `official_api` | `SamsungCrawler` | 삼성전자 | HTTP requests (driver=None) |
| `official_selenium` | Naver·Kakao·SKHynix·Krafton·Hybe·SM·Hyundai·Kia | 8개사 | Selenium SPA/ATS |
| `recruiter_kr` | `RecruiterKrCrawler` | 셀트리온·유한양행·HL만도 | recruiter.co.kr 집계 |
| `simple_site` | `SimpleSiteCrawler` | 한미반도체·스튜디오드래곤·삼성바이오로직스 | requests + Selenium fallback |

#### 드라이버 로테이션
헤드리스 Chrome은 45+ 페이지 이동 후 메모리 누적 → 크래시. 기본 `driver_rotation_size=3`으로
**3개 기업마다 Chrome 재시작**. 로테이션 시 DB 재조회 없이 `_instantiate_crawlers`로 driver만 교체.

### 분석기 (`app/analyzers/hiring/`)

**분석기가 두 개입니다. 역할이 다르므로 import 경로에 주의하세요.**

| 파일 | 역할 |
|------|------|
| `hiring_analyzer.py` | **배치 엔진** — `analyze_hiring_trend(target_date)`. asyncpg로 DB 직접 쿼리, 14일 이동평균 + 3단계 Fallback → `hiring_signals` UPSERT(executemany). 일별 크론잡 전용. |
| `analyzer.py` | **Analyzer Protocol 준수** — `analyze(stock_code, evidence) → SourceResult`. 수집 rows를 받아 DB 쿼리 없이 순수 연산. 오케스트레이터/Aggregator 연동용. |

### 스크립트 (`script/`)

| 파일 | 실행 주기 | 역할 |
|------|----------|------|
| `bootstrap_hiring_baseline.py` | 최초 1회 + 분기 1회 | 네이버 DataLab 3년치 트렌드 → `hiring_baseline` UPSERT (기업당 `search_grouped` 1회) |
| `run_daily_hiring_pipeline.py` | 매일 (cron / Airflow) | Step 1 수집 → Step 2 분석 자동화 |
| `dashboard_validator.py` · `db_explorer.py` | 수동 | Streamlit 데이터 정합성 검증·DB 탐색 (dev 도구, PR #275) |

---

## 수집 방어 레이어

크롤 안정성·효율·복구를 위한 4개 레이어. (이전 문서에 누락되어 있던 핵심 메커니즘.)

### 1. Anti-block (`sites/http.py`) — requests 경로
`get()`이 매 시도 **UA 로테이션** + **재시도/지수 백오프(+지터)**, **429/403 적응형 백오프**(429는 `Retry-After` 존중) 적용.
timeout·커넥션오류·5xx는 지수 백오프, 그 외 4xx는 즉시 raise. Settings(`HIRING_*` env)로 제어.

### 2. 차단 신호 센서 (#162 트리거 계측)
`http.py`의 모듈 카운터 `_BLOCK_SIGNALS`가 requests 경로 **403/429 발생 횟수**를 런 단위 집계.
`collect()` 시작 시 `reset_block_signals()`, 종료 `finally`에서 요약 로깅:
- 0건 → `🛡️ 차단 신호 없음`, >0건 → `🚧 차단 신호 감지` **WARNING**(프록시 인프라 도입 재검토 트리거).
> Selenium 경로 차단은 신호가 모호해 제외 — 센서는 측정 가능한 requests 403/429만 정직하게 잰다.

### 3. 수집단계 선거부 (#176)
포털 키워드 검색은 하청·대리점·계열사 공고를 대거 유입시킨다(미등록). `collect()` 반환 직전
`_reject_unregistered`가 미등록 레코드를 **parse 이전에 드랍**해 다운스트림 처리량·트래픽 낭비를 줄인다.
- 매칭은 insert 단계 `_resolve_stock`과 **동일한 `_match_stock_row`**(ILIKE name/short_name + 부분일치)를 공유 →
  유효 공고 유실(회귀) 불가. insert 단계 게이트는 이중 방어선으로 유지.
- DB 연결 실패 시 전량 통과(graceful degradation).

### 4. 격리·KST
- **격리(#161)**: 파싱 실패 원본을 `hiring_quarantine`에 덤프. 크롤러가 `CollectorResult`(frozen dataclass,
  `.data` + `raw_payload`)를 반환하면 원본 HTML/JSON 보존 → selector 수정 후 유실 제로 replay-reparse.
  `collect()`의 결과 리스트에는 dict(legacy)와 `CollectorResult`(new)가 **혼재** 가능(소비자는 양형 처리 필수).
- **KST 정합성(#120/#253)**: `posting_date`/`observed_date` 등 날짜 경계를 한국시간 자정 기준으로 고정.
  UTC 서버에서 KST 00:00~09:00 수집분이 전날로 오분류되는 문제 방지.

### 5. 지표 정의 + Warming-up 가드 (#290)
> **[지표 정의 명세]** hiring `job_count`는 특정 시점의 **'열린 공고 총량(Inventory Volume)'이 아니라,
> '당일 최초 발견된 신규 공고 수(Discovery Velocity)'**를 의미한다(공고 1건=1행, `(source_type, job_link)`
> dedup으로 `observed_date`는 최초 발견일 1회만 기록).

- **Warming-up 가드**: 수집 커버리지 급변이 가짜 가속도 신호를 만든다(예: 공식 careers 첫 전체 스크랩 →
  하루에 수백 건이 "신규"로 몰림). 그래서 **소스 최초 등장·장기(>5일) 공백 후 재개** 시점의 (소스,날짜)
  데이터는 분석 모집합에서 **제외**한다(소스 배제·종목 유지: 같은 종목의 정상 소스는 그대로).
- 적용 위치: 신규 경로 `evidence_loaders/hiring_loader.py`(`_drop_warming_up`). 레거시 `hiring_analyzer.py`는
  컷오버(#188) 폐기 예정이라 미적용. 휴리스틱 한계(간헐 정상 공고 억제 가능)는 코드 주석에 명시.
- 참고: 신규 경로는 현재 `published_at`, 레거시는 `observed_date` 기준(현재 0행 차이지만 향후 통일 필요 — #188).

---

## 환경 변수

`.env.example` 참조. 채용 파이프라인 필수:

```dotenv
DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha

# 네이버 DataLab — 채용 기준선 부트스트랩 전용 (별도 앱, DataLab 파트와 쿼터 분리)
HIRING_DATALAB_CLIENT_ID=
HIRING_DATALAB_CLIENT_SECRET=
```

> `HIRING_DATALAB_*`은 `NAVER_DATALAB_*`(DataLab 극성 파트)과 **다른 앱**입니다.
> 쿼터를 공유하면 429가 발생하므로 반드시 분리 등록하세요.

---

## 실행 방법

### 사전 조건
```bash
uv sync                                   # 의존성(워크스페이스). pip install 금지
python database/migrate.py apply --seeds  # 스키마 + 시드 적용
# Chrome 120+ (WebDriver Manager가 ChromeDriver 자동 설치)
```

### 1. 기준선 부트스트랩 (최초 1회 + 분기 1회)
```bash
cd services/agent-worker
uv run python script/bootstrap_hiring_baseline.py
```
- is_target 기업 전체에 대해 3년치 주간 트렌드 수집 → `hiring_baseline` UPSERT.
- 기업당 `search_grouped` **1회 호출**(키워드 한 그룹) + 루프 `sleep(0.5)` → 일일 쿼터 내 안전.
- 검증: `SELECT count(*), min(avg_search_volume) FROM hiring_baseline;` (행수=타깃, avg>0).

### 2. 일일 파이프라인
```bash
cd services/agent-worker
uv run python script/run_daily_hiring_pipeline.py              # 오늘(KST)
uv run python script/run_daily_hiring_pipeline.py --date 2026-06-12
```
```
Step 1: MultiSourceCrawler.run()  → 수집 + 선거부 → 3계층 적재 (extra_payload에 seasonal_baseline)
   ↓
Step 2: HiringAnalyzer.analyze_hiring_trend()  → hiring_signals UPSERT (executemany)
```
로그: `logs/hiring_pipeline.log`

### 3. 대화형 수동 실행
```bash
uv run python app/collectors/hiring/main.py
```

### 4. 기준선 재구축 후 캐시 비우기 (FastAPI 장기 가동 시)
```python
await HiringAnalyzer.clear_cache()  # 다음 analyze 호출 시 hiring_baseline 재로드
```

---

## 채용 분석 로직 — 3단계 Fallback

`HiringAnalyzer.analyze_hiring_trend(target_date)` (배치 엔진) 흐름:

**Step 1.** 지난 14일 이동평균 — 공고 0건인 날(행 없음)을 분모에서 빼지 않도록 `COUNT/14.0` 사용
```sql
SELECT stock_id, COUNT(raw_document_id) / 14.0 AS rolling_avg
FROM hiring_raw_details
WHERE observed_date BETWEEN :date - 14 AND :date - 1
GROUP BY stock_id
```

**Step 2.** 3단계 Fallback — `_get_baseline_scale(stock_id, rolling_avg) → (scale, phase)`

| Phase | 조건 | 기준선 | 신뢰도 |
|-------|------|--------|--------|
| **A** — Day 14+ | `rolling_avg ≥ MIN_ROLLING_AVG_THRESHOLD` | 14일 이동평균 | 높음 |
| **B** — Cold Start | rolling_avg 부족, DataLab 데이터 있음 | `max(avg_search_volume / 100, 0.5)` | 중간 |
| **C** — 데이터 없음 | 기준선 전무 | `DEFAULT_BASELINE_SCALE = 1.0` | 낮음 |

> **`MIN_PHASE_B_EXPECTED = 0.5`**: 검색량 낮은 소형주의 분모가 0에 가까워져 relative_strength가 폭발하는 것 방지.
> **`MIN_TODAY_JOB_COUNT = 3`**: 0→1건 같은 일상 변동의 Spike 오판 방지(오늘 공고 3건 미만이면 스킵).

**Step 3.** 상대 강도 / Spike 판정
```python
expected = base_scale * seasonal_factor          # seasonal_factor: 현재 분기 q_factor (Phase C=1.0)
if expected <= 0: expected = DEFAULT_BASELINE_SCALE
relative_strength = (today_count / expected) * 100
is_spike = relative_strength >= 150              # HIRING_SPIKE_THRESHOLD = 1.5
```

**Step 4.** `hiring_signals` 배치 UPSERT (executemany, 단 1회 네트워크 왕복)
```sql
INSERT INTO hiring_signals (stock_id, observed_date, job_count, baseline,
    relative_strength, is_spike, calculation_phase)
VALUES (...) ON CONFLICT (stock_id, observed_date) DO UPDATE SET ...
```

### Protocol 분석기 (`analyzer.py`)
오케스트레이터가 수집 rows(evidence)를 전달하면 DB 쿼리 없이 `SourceResult`(direction/score/summary) 반환.
Phase별로 summary 문구를 동적 생성(A=14일 평균, B=트렌드 기준, C=기본 기준선)해 상위 LLM 혼란 방지.

---

## Zero-Hardcoding 설계

기업 정보는 DB가 Single Source of Truth. 코드 수정 없이 SQL로 관리:

| 변경 | SQL |
|------|-----|
| 새 기업 추가 | `stocks` INSERT + `hiring_sources` INSERT |
| 수집 제외 | `UPDATE stocks SET is_target = FALSE WHERE ticker = '...'` |
| 약칭 변경 | `UPDATE stocks SET short_name = '...' WHERE ticker = '...'` |
| 공식 크롤러 교체 | `UPDATE hiring_sources SET crawler_class = '...' WHERE stock_id = ...` |

`crawler_type` 값: `official_api` / `official_selenium` / `recruiter_kr` / `simple_site`.
(포털 사람인·잡코리아는 전체 기업 키워드 검색이라 `hiring_sources` 불필요.)

---

## 테스트

```bash
cd services/agent-worker

uv run pytest tests/ -k hiring -q                          # 채용 관련 전체 (~238케이스 / 23파일)
uv run pytest tests/analyzers/test_hiring_analyzer.py -q   # 배치 엔진 (3단계 Fallback)
uv run pytest tests/test_hiring_keyword_generator.py -q    # 키워드 생성 (16케이스)
uv run pytest tests/collectors -q                          # 수집기 전체 (178케이스)
```

대표 커버리지(파일):
- `collectors/test_hiring_multi_source_crawler.py` — 크롤러 매핑·로테이션·**수집단계 선거부**·**차단 센서**
- `collectors/test_hiring_http.py` — anti-block 재시도/백오프 + **403/429 센서**
- `collectors/test_hiring_quarantine.py` · `test_hiring_validation_gate.py` · `test_hiring_observed_date.py`(KST)
- `analyzers/test_hiring_analyzer.py` — Phase A/B/C 전환, Spike 경계, executemany UPSERT
- `test_hiring_keyword_generator.py` — Naver API 규격(list[str]) 검증

> 비동기 테스트는 `unittest.IsolatedAsyncioTestCase` 사용(`pytest-asyncio` 불필요).

---

## 트러블슈팅

### 마이그레이션 미적용
```
psycopg2.errors.UndefinedTable: relation "hiring_baseline" does not exist
```
→ `python database/migrate.py status`로 적용 상태 확인 후 `apply --seeds`.

### 네이버 DataLab 429 Too Many Requests
→ `HIRING_DATALAB_*`과 `NAVER_DATALAB_*`이 동일 앱을 쓰는지 확인. 네이버 개발자 센터에서
채용 전용 앱을 **별도 등록**해 쿼터를 분리하세요.

### 기업 등록 오류 (`_SkipRecord`)
```
WARNING: ⚠️  DB stocks 에 미등록 기업 → 스킵: ...
```
→ `stocks`에 기업이 없거나 `is_target=FALSE`. 단, 미등록 노이즈는 정상(수집단계 선거부가 의도적으로 드랍).
```sql
SELECT ticker, name, is_target, short_name FROM stocks WHERE is_target = TRUE;
UPDATE stocks SET is_target = TRUE WHERE ticker = '000000';
```

### 차단 신호 감지 (`🚧`)
로그에 `🚧 차단 신호 감지: 403=N 429=M`이 뜨면 단일 IP 차단이 시작된 것 — 프록시 로테이션 인프라(#162)
도입을 재검토하는 트리거입니다. 0이면 단일 IP로 정상.

### Cold Start (14일 데이터 부족)
처음 2주는 Phase B(검색량 기반)로 동작하며 신뢰도 낮음. `bootstrap_hiring_baseline.py` 실행 여부와
`hiring_signals.calculation_phase = 'B'`로 확인.

### Chrome WebDriver 버전 불일치
WebDriver Manager가 자동 다운로드. 오프라인이면 수동 설치 필요(Chrome 120+ 권장).

### Windows 한글 깨짐 / 파이프라인 타임아웃
`main.py`는 UTF-8 강제. 그래도 깨지면 PowerShell: `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`.
파이프라인 단계 타임아웃 기본 3600초, 15개사 멀티소스 크롤은 통상 300~600초(`logs/hiring_pipeline.log` 확인).
```
