# Hiring 파이프라인 컷오버 기준 & `final_signals` 네이밍 정리

> 상태: 제안(Draft). 결정 필요 항목은 **[DECISION]** 로 표시.
> 관련: `parity_hiring.py`, `app/analyzers/hiring/{hiring_analyzer,analyzer}.py`,
> `script/run_daily_hiring_pipeline.py`, `run_analyzers.py`,
> `app/orchestrator/alternative_persistence.py`, `database/migrations/001_baseline.sql`

---

## 1. 배경 — 현재 Hiring 신호는 이중 파이프라인

| | 레거시 | 신규 |
| --- | --- | --- |
| 분석기 | `app/analyzers/hiring/hiring_analyzer.py` | `app/analyzers/hiring/analyzer.py` |
| 엔트리 | `script/run_daily_hiring_pipeline.py` (야간 배치 cron) | `run_analyzers.py` (큐 harness) |
| 시간 의미 | **정확 날짜** (`observed_date = date` 인 공고만) | **lookback 윈도우** (`lookback_days`) |
| 기준 | **14일 이동평균 baseline × 분기 계절가중치** → `relative_strength %` | 직군/섹터수요 컨텍스트 기반 **`score ∈ [-1,+1]` + `direction`** |
| 출력 테이블 | `hiring_signals` | `final_signals` |
| 가동 | **현재 운영 경로** (cron으로 매일 적재) | **opt-in** (compose `profiles: ["analyzer"]`, 기본 제외) |

두 출력은 스케일이 달라 숫자 직접 비교가 불가능하다. `parity_hiring.py` 는 둘을 **UP/FLAT/DOWN 버킷**으로 환산해 *방향 일치율*만 측정한다(DB write 0, 레거시 INSERT는 캡처·미실행).

이 병행은 **영구 상태가 아니라 이행기(strangler-fig 컷오버)** 다. 최종 목표는 **하나만 남기는 것**이며, 그 근거가 parity다.

### 1.1 두 경로의 의미 차이 (코드 근거)

같은 "채용이 늘었나/줄었나"를 묻지만, **무엇을 언제 기준으로 재는지**가 다르다. parity의 MISMATCH/PARTIAL은 대부분 이 차이에서 나온다.

**① 시간 범위 — 정확 날짜(레거시) vs lookback 윈도우(신규)**
- **레거시**는 `--date` **당일에 공고가 있는 종목만** 본다. baseline은 직전 14일을 14.0으로 나눈 일평균
  (`app/analyzers/hiring/hiring_analyzer.py:184-204` — `observed_date BETWEEN $1 - 14d AND $1 - 1d` 로 일평균,
  `observed_date = $1` 로 당일 건수). → 당일 공고가 0이면 그 종목은 **행 자체가 없음**(parity에서 `NO_DATA` → `PARTIAL`).
- **신규**는 `as_of` 기준 **최근 `lookback_days` 윈도우**를 누적해 본다
  (`app/analyzers/hiring/analyzer.py:44-49` → `compute_indicators(rows, as_of, lookback_days, sector_demand)`).
  → 정확히 당일 공고가 없어도 윈도우 안에 있으면 신호가 잡힌다.
- **귀결:** "정확 당일엔 공고 없지만 최근 며칠 추세는 있는" 종목 = 레거시 NO_DATA · 신규 신호 → **PARTIAL**(충돌 아님,
  `parity_hiring.py:20-23, 89-93`).

**② 기준·산식 — 14일 baseline×계절가중(레거시) vs 직군분류 컨텍스트 점수(신규)**
- **레거시**: `상대강도% = 당일건수 / (baseline × 분기 계절가중치) × 100`, `≥150%` 면 `is_spike`
  (`hiring_analyzer.py:230-238`). baseline은 3단계 fallback — Phase A(14일 이동평균) → B(네이버 검색량) → C(기본값)
  (`hiring_analyzer.py:128-146`). 출력은 `relative_strength %` + `is_spike(bool)` → `hiring_signals` 에 적재.
- **신규**: 윈도우 지표를 룰로 평가해 `score ∈ [-1,+1]` + `direction(positive/negative/neutral)` 산출
  (`analyzer.py:50, 95-104` → `evaluate_indicators`). 직군 분류(`app/analyzers/hiring/job_functions.py`)·섹터수요
  컨텍스트가 반영되고, 채용 **직무·기술 포커스**는 점수와 무관한 서술 근거로만 첨부(`analyzer.py:63-93`).
  이력 부족/오래된 데이터면 `data_status="partial"`(`analyzer.py:54-55`).

**③ 버킷 환산(비교를 위한 공통자)**: 레거시 `is_spike` 또는 `rs≥150%`→UP, `rs<100%`→DOWN, 그 사이 FLAT
(`parity_hiring.py:56-75`); 신규 `positive`→UP, `negative`→DOWN, `neutral`→FLAT(`parity_hiring.py:78-86`).
둘 다 데이터가 있을 때만 `MATCH/MISMATCH`, 한쪽이라도 NO_DATA면 `PARTIAL`.

> 요약: **레거시 = "오늘 하루, 평년 대비 몇 %인가"(point-in-time, 비율·스파이크)**,
> **신규 = "최근 며칠 추세가 어느 방향인가"(window, 방향·점수 + 직군맥락)**. 컷오버는 이 의미 차이를
> 인지한 채 *방향*이 일관되는지를 본다.

---

## 2. [DECISION] 컷오버 완료 기준 (레거시 폐기 조건)

아래 조건이 **모두** 충족되기 전에는 레거시(`hiring_analyzer.py` + `run_daily_hiring_pipeline.py` + `hiring_signals`)를 삭제하지 않는다.

### C0. 사전 조건 — parity가 실행 가능할 것 ⚠️ **prod 기준 미충족(블로커)**
- `parity_hiring.py` 가 대상 DB에서 **에러 없이 끝까지 실행**되어야 한다.
- **prod 상태(2026-06-16 확인):** 운영 DB에서 `hiring_raw_details.observed_date` 컬럼 부재로
  `UndefinedColumnError` 발생 → prod 대상 parity 실행 불가. DB 스키마가 `001_baseline` 보다 뒤처져 있음
  (스키마 드리프트). **이 드리프트 해소가 prod 컷오버의 1번 선결 과제이며, prod 마이그 재정합은 팀 결정 사항.**
- **로컬 우회(2026-06-18):** 로컬 Docker Postgres는 `001_baseline` 최신이라 `observed_date` 가 있어
  C0 스키마 블로커가 없다. 단 과거 `parity_hiring.py` 는 `ssl="require"` 하드코딩이라 로컬(SSL 미지원) 연결이
  막혔는데, `run_analyzers.resolve_ssl(host)` 재사용으로 해소(로컬→SSL off, prod→require, `DB_SSL` override).
  → **로컬 시드 데이터로 parity를 돌려 C1/C2 측정 방법을 선검증**할 수 있다(아래 §5 절차). prod 재정합 전
  산식·버킷 로직 회귀는 로컬에서 확인 가능.

### C1. 방향 일치율 임계
- `agreement_rate`(= MATCH / (MATCH+MISMATCH)) **≥ 0.85** 를, 서로 다른 **최소 N=3 영업일**에 대해 재현.
  - *(임계값 0.85·N=3 은 제안값. 운영 위험도에 맞게 조정 — [DECISION])*

### C2. MISMATCH 전수 해명
- 잔존 `MISMATCH` 행 각각에 대해 "왜 갈렸는지" 한 줄 사유를 남기고, **의도된 차이**(예: lookback이 잡고
  정확날짜가 못 잡는 추세)인지 **버그**인지 분류. 버그는 0건이어야 함.

### C3. PARTIAL 비율 인지
- `PARTIAL`(한쪽 NO_DATA, 주로 정확날짜에 공고 없음)은 충돌이 아님. 다만 **PARTIAL 비율이 과도하면**
  (예: comparable 표본이 전체의 30% 미만) 일치율 통계의 신뢰구간이 좁아지므로, 표본 부족으로
  **C1을 만족했다고 보지 않는다**.

### C4. 다운스트림 전환 확인
- `hiring_signals` 를 읽는 소비자(있다면)가 신규 경로(`final_signals` 또는 후속 통합 산출)로 전환됐는지 확인.
- `hiring_signals` 를 FK/참조하는 객체가 없는지 스키마 확인.

### C5. 운영 전환
- 신규 `analyzer` 서비스(또는 cron 1-pass)가 **레거시와 동일 주기로 단독 가동**되어 N일간 무사고.
- compose `profiles: ["analyzer"]` 제거 또는 스케줄러 전환으로 **신규를 기본 경로로 승격**.

### 완료 액션 (위 전부 충족 시)
1. `run_daily_hiring_pipeline.py` 스케줄 중단.
2. `hiring_analyzer.py` / `run_daily_hiring_pipeline.py` / `hiring_signals`(및 전용 컬럼 `calculation_phase`) 제거 — 별도 PR.
3. `parity_hiring.py` 도 역할 종료 시 제거(또는 회귀 보관용으로 명시).

> 메모리 노트 [[hiring-pipeline-cutover]] 의 "parity 통과 전 삭제 금지" 규칙을 위 C0~C5로 구체화한 것.

---

## 3. `final_signals` 네이밍 — 현재 의미와 모호함

### 3.1 현재 `final_signals` 가 실제로 담는 것
`database/migrations/001_baseline.sql` 의 `final_signals`:
- 키: `(stock_id, signal_date, run_key, version)` UNIQUE, `is_current` 로 최신행 단일화.
- 컬럼: `final_score(0-100)`, `confidence`, `signal(positive/negative/neutral/**mixed**)`,
  `source_agreement(HIGH/MEDIUM/LOW)`, `warning_level`, `score_breakdown(JSONB)`, `summary`,
  `bull_point`/`bear_point`, `disclaimer`, `is_published`, `min_plan_required(free/pro/premium)`,
  `consensus_score`, `positive_evidence`/`caution_evidence`.
- 참조: `score_history`, `backtest_results`, `signal_journals`, `user_signal_reads` 가 FK로 물려 있음.

즉 **사용자에게 발행되는 "종목별 최종 발행 신호"** 로 설계됨(발행 플래그·플랜 게이팅·면책고지 포함).

### 3.2 그런데 현재 **유일한 생산자는 Alternative 집계기뿐**
- `app/orchestrator/alternative_persistence.py` 의 `AlternativeSignalPersistence` 만 `final_signals` 를 씀.
- 이건 **hiring 전용이 아니라** hiring + patent + datalab 을 `AlternativeAggregator` 로 **교차 집계한 1행**.
- DART/리포트/가격(밸류에이션)은 **아직 `final_signals` 를 쓰지 않음**.
- 게다가 현재 행의 `run_key` 기본값은 **`"BATCH"`** (`AnalyzerRuntimeConfig.run_key`) — 생산자를 식별조차 못 함.

### 3.3 모호함의 정체 (당신이 느낀 지점)
1. **스코프 모호:** "final" 이 *무엇의* final인지 이름이 말해주지 않음. 지금은 사실상 "alternative의 final"인데
   이름은 "전부의 final"처럼 읽힘. hiring 전용으로 오해할 여지도 있음.
2. **미래 충돌:** DART + 리포트 + 대체데이터가 합류할 때 두 모델이 가능한데, 이름이 어느 쪽도 못 박음 —
   - **(A) 융합 모델:** 모든 소스가 **하나의 진짜 최종 행**으로 합쳐짐 → 이때 "final" 은 *옳은* 이름.
     하지만 그러려면 alternative가 `final_signals` 를 **직접** 쓰면 안 되고, 도메인별 중간 산출 후
     **융합 단계**가 final을 만들어야 함. 지금은 융합 단계가 없어 alternative가 곧장 final을 써서
     **이름이 시기상조/과대**.
   - **(B) 파이프라인별 모델:** 각 파이프라인이 자기 도메인 final을 `run_key` 로 구분해 적재
     (예: `run_key='ALTERNATIVE'|'DART'|'REPORT'`). 그러면 `(stock_id, signal_date)` 당 **여러 "final" 행**이
     공존 → *어느 것도 진짜 최종이 아님* → "final" 은 **오해를 유발**. 더 맞는 이름은
     `published_signals` / `signal_outputs` 류.

> 핵심: **이름 문제의 근원은 아키텍처 미결정**이다 — "최종 1행 융합" 인가, "도메인별 발행 + 별도 융합" 인가.

### 3.4 [DECISION] 권고
- **단기(낮은 리스크, 리네임 없음):**
  1. 본 문서로 "현재 `final_signals` = Alternative 교차집계 발행신호, `run_key` 가 생산자 구분자" 임을 명시.
  2. alternative 생산자의 `run_key` 를 **`BATCH` → `ALTERNATIVE`**(또는 `ANALYZER_RUN_KEY=ALTERNATIVE`)로
     설정해 행을 **자기설명적**으로 만든다. (DDL 변경 없음, 값만)
- **중기(아키텍처 확정 후):**
  - **(A) 융합 모델 채택 시:** 레이어를 분리 — 도메인별 신호(예: `alternative_signals`, `dart_signals`...) →
    **융합 산출이 `final_signals` 를 단독 생성**. `final` 이름 유지가 정당해짐.
  - **(B) 파이프라인별 모델 채택 시:** `final_signals` → `published_signals`(또는 `signal_outputs`) 로 **리네임**하고,
    `source`/`run_key` 로 도메인을 명시. "진짜 최종 융합" 개념이 필요해지면 그때 별도 테이블/뷰로 둔다.
- 어느 쪽이든 **DART·리포트 합류 전에 결정**해야 하며, 결정 전까지는 단기 권고(자기설명적 `run_key`)로 모호함을 줄인다.

> 리네임은 FK 다수(`score_history`/`backtest_results`/`signal_journals`/`user_signal_reads`)와 트리거
> (`set_final_signal_current`)에 파급되는 **무거운 마이그레이션**이므로, 본 문서의 결정 없이 선행하지 않는다.

---

## 4. 다음 액션
- [ ] **C0 블로커(prod)**: 운영 DB 스키마 드리프트(`hiring_raw_details.observed_date` 등) 재정합 → prod parity 실행 가능화(팀 결정).
- [ ] parity 재실행하여 C1 표본(≥3 영업일) 수집, `agreement_rate`/MISMATCH 기록.
- [ ] §3.4 [DECISION]: 융합(A) vs 파이프라인별(B) 아키텍처 확정.
- [ ] 단기 조치: alternative `run_key = ALTERNATIVE` 적용.

---

## 5. 로컬 parity 실행 절차 (prod 재정합 전 선검증용)

prod C0(스키마 드리프트)는 팀 결정 사안이라, 그 전에 **산식·버킷 로직과 측정 방법을 로컬에서 선검증**한다.
로컬 Docker Postgres는 `001_baseline` 최신이라 `observed_date` 가 있고, SSL 보정으로 연결도 열렸다.

1. **로컬 DB 기동 + 마이그**: `docker compose up -d postgres` 후 `database/migrations` 적용(`001_baseline` 포함).
2. **hiring raw 시드**: `hiring_raw_details` 에 며칠치 공고 행을 적재(수집기 실행 또는 fixture INSERT).
   - 의미 차이(§1.1)상 **정확 날짜에 공고가 있는** 종목이 있어야 레거시가 `NO_DATA`(PARTIAL)가 아닌 비교행을 만든다.
3. **환경**: `DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha`.
   로컬 호스트는 `resolve_ssl` 이 자동으로 SSL을 끈다(필요 시 `DB_SSL=disable` 명시).
4. **실행(읽기 전용)**:
   ```bash
   cd services/agent-worker
   python parity_hiring.py --date YYYY-MM-DD            # 그날의 hiring 종목 전체
   python parity_hiring.py --ticker 005930 --json out.json
   ```
   리포트의 `agreement_rate(over comparable)` 와 `MISMATCH` 행이 C1/C2 측정값이다(DB write 없음).
5. **해석**: `MISMATCH` = 컷오버 리스크 목록(§C2 전수 해명 대상), `PARTIAL` = 한쪽 NO_DATA(주로 정확 날짜 공고
   부재, §C3 충돌 아님). 임계는 §C1(`≥0.85`, ≥3 영업일).

> 주의: 로컬 표본은 시드 데이터에 의존하므로 **C1 충족 근거로 직접 쓰지 않는다**(C1은 prod/실데이터 기준).
> 로컬은 "parity가 끝까지 돌고 산식·버킷이 의도대로 동작하는지" 확인하는 용도다.
