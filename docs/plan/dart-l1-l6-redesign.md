# DART L1~L6 재설계 — 학습형 메타러너 기반 소스 융합 (v2)

> 영역: agent-worker. 앱 루트: `services/agent-worker/app`.
> v1(결정론 소스 집계)에서 **v2(학습형 메타러너 일원화)** 로 방향 전환.

## 배경 (왜 하는가)
DART 분석 결과가 모든 종목/공시에서 `final_score=50/neutral/CAUTION`로 고정된다(실측 005930
43건 전부 동일). 근본 원인:
1. `app/analyzers/dart/rules.py classify_dart_report`가 positive/negative를 안 냄 →
   `source_result.py _score_delta=0` → `orchestrator/dart/tasks.py _to_db_score=(0+1)*50=50`.
2. L1/L2/L3(`dart_financial_facts`·`dart_ownership_events`·`dart_employee_stats`)는 수집·sync·
   마이그(006/011/013)까지 있으나 큐/핸들러 미배선, 어떤 analyzer도 안 읽음.
3. 단일 소스 집계 → source_agreement=LOW / consensus=50 / CAUTION.

### 설계 비판(채택) — 결정론 집계의 한계
현재 **소스 판정은 전부 결정론 집계(`AGGREGATE_SIGNAL`)** 이고, **메타러너(`META_COMBINE`)는 변동성
전용**이다(검증: `meta_learner.py:8-10` "방향성은 AGGREGATE 책임, 메타러너는 vol 크기만"). DART뿐
아니라 hiring/patent/datalab/report/price 전부 결정론 집계로 `final_signal`을 만들고, `combined_vol`은
`AGGREGATE`가 **읽지도 않으며**(import 없음) SYNTHESIZE/recommend 하류에서만 소비된다. 즉 결정론으로
판정한 소스 점수를 끼워 맞추는 구조라, 근거 있게 학습 융합되지 않는다.

### 결정 (v2)
- **전체 L1~L6 재설계 + 소스 융합을 학습형 메타러너(L8)로 일원화**.
- **L1~L6은 "판정기"가 아니라 "결정론 피처 추출기"로 재정의.** 모든 소스(DART/hiring/patent/datalab/
  report) + vol 모델 출력을 공통 피처/base 예측으로 만들고, **forward-return 라벨(L6)로 학습한
  stacking 메타러너**가 최종 `final_score/direction/confidence`를 융합한다.
- 결정론 집계(`AGGREGATE`)는 **cold-start/폴백 베이스라인**으로 보존(학습 가중·라벨 부족 시).
- per-source 생성형 LLM 판정 제거, **파싱 LLM(L5)만 허용**. 끝단 SYNTHESIZE는 생성형 LLM 유지.
- 기대 결과: 차등 final_score + 모든 소스가 동일한 학습 융합 경로 + L6 lift로 신호 채택 검증.

## 현 구조 사실 (검증 완료)
- 메타러너 `app/ml/meta_learner.py combine`: vol 4모델(ewma/har_rv/garch/lightgbm) `pred_vol`
  등가평균(`equal_fallback`, 학습 가중 `artifacts/meta_learner.json` 부재). 신뢰도=1−변동계수.
  **vol 가정 1차원 결합기** — 이질 피처/방향 점수를 그대로 못 섞음.
- `ml_inferences.pred_value`는 `DOUBLE`, `model_name VARCHAR(50)` 임의 → **일반 점수 수용 가능**
  (스키마는 vol 전용 아님). 단 `DataContract`는 OHLCV **9컬럼 고정**(`data_contract.py:19-29`) →
  피처 통로 확장 필요.
- `app/orchestrator/aggregation/tasks.py`는 `meta_signals` 미참조. 소스 전부 결정론 집계.
- `packages/vol-models/.../cpu_lgbm.py:48-50`에 **alt-data(dart) 피처 TODO 예약**(이미 의도된 확장점).
- **라벨 출처 = L6 event_study forward return** → 학습 메타러너의 선결 조건.
- 점수 합류 인프라: `analysis.py list_latest_source_results_for_stock`가 `run_key LIKE 'DART%'/
  'PRICE%'/...` fan-in. `app/orchestrator/price/tasks.py`가 per-stock agent_result emit 템플릿.

## 목표 아키텍처
```
[수집/적재] L1~L6 + 대체데이터(hiring/patent/datalab) + OHLCV
      | (결정론 피처 추출 — L1~L6/ALT analyzer)
      v
[피처 레이어] 소스별 -> 공통 as-of 피처벡터 (PIT/known_at, feature store)
   DART: 재무비율·YoY(L1), 내부자 z(L2), 고용 YoY(L3), 톤(L4), 관계(L5)
   ALT : hiring/patent/datalab 지표   PRICE/vol: ewma/har/garch/lgbm pred_vol
      v
[ML/DL 추론] base 모델들이 피처 -> 예측(vol + 소스별 base score)  -> ml_inferences
      v
[학습형 메타러너 L8] forward-return 라벨(L6)로 학습한 stacking이 모든 base 예측·피처 융합
      -> final_score / direction / confidence / risk   ([cold-start] 학습 부재 시 결정론 집계 폴백)
      v
[게이트/RISK_VETO] -> SYNTHESIZE(LLM 설명, 수치 불변) -> 발행
      ^
[L6 백테스트] event_study forward return = 학습 라벨 + lift 채택 게이트
```

## Phase 순서 (의존성 재배치)
핵심 의존성: **피처(L1~L4) + 라벨(L6) → 메타러너 학습(L8)**. 그래서 L6가 L8보다 앞선다.
권장 1차 마일스톤: Phase 0~4(피처화) + Phase 6(라벨) → Phase 7(학습 융합) 최소 경로로 "결정론 탈출 +
학습 융합" 입증.

### Phase 0 — rules.py 핫픽스 (즉시, 단독 PR)
`app/analyzers/dart/rules.py classify_dart_report`에 결정론 방향 매핑(positive: 공급계약/자기주식취득/
무상증자/흑자전환/배당; negative: 유상증자·CB/감자/관리종목·상폐/감사의견 거절·한정/횡령·배임/적자전환).
이 결정론 방향은 **메타러너 cold-start 폴백 + base 피처**로 재활용된다(버려지지 않음). 테스트 추가.

### Phase 1 — per-source 생성형 LLM 판정 제거 + 파싱 클라이언트 분리·보존
`agents/dart/agent.py`·`graph.py`·`orchestrator/dart/tasks.py`·`queue/handlers.py`의 llm_analyzer 판정
경로 제거. `analyzers/dart/llm.py`의 HTTP 클라이언트/JSON 파서는 신규 `app/llm/clients.py`로 추출해
L5 파싱이 재사용, 판정부(DartLlmAnalyzer.analyze/should_use_dart_llm/_build_prompt) 삭제.

### Phase 2~4 — L1/L2/L3 결정론 피처 추출기 + signal_events emit
**판정이 아니라 피처**를 산출하도록 구현:
- L1 `financial_metrics.py`: revenue/op_income YoY·QoQ, debt_ratio, turnover, interest_coverage,
  capex_trend, earnings_quality(OCF−NI). 분모0/음수 가드, magnitude=z-score.
- L2 `ownership_signal.py`: 내부자 순매수/매도 ratio_delta z-score. L3 `employee_signal.py`:
  headcount YoY.
- 각 레벨 `orchestrator/dart/{financials,ownership,employee}_tasks.py`: Collect(기존 Sync 재사용)/
  Normalize(파생→`upsert_source_document`(rcept_no)+`upsert_signal_event`+`upsert_signal_metric`)/
  Analyze(run_key=`DART_FIN`/`DART_OWN`/`DART_EMP`, PRICE 패턴 미러). task_types/handlers/scheduler/
  e2e 배선. signal_events는 **근거추적 + L6 백테스트 라벨 부착**용으로 필수.

### Phase 5 — 피처 파이프라인 (소스 → 공통 피처벡터)
- `DataContract`(OHLCV 9컬럼) 확장 또는 별도 **feature assembly/feature store**(`get_features(ticker,
  asof)`): 모든 소스 피처를 `date` 기준 **as-of join + PIT(known_at ≤ asof)**. 누설 차단.
- `app/ml/contract_adapter.build_contract`가 OHLCV 외 소스 피처를 실어 보내도록 확장, `ml_infer`가
  피처 조회해 합류. (cpu_lgbm `_features`에 dart 컬럼 추가 — 기존 TODO 활성화.)

### Phase 6 — L6 백테스트 (라벨 + 채택 게이트) ★메타러너 선결
- 마이그(`migrate.py new "event_study_panel_l6"`): event_study_panel(signal_event_id FK, fwd_return_
  1d/5d/20d, abnormal_return_20d, benchmark, uq signal_event_id). 리포 `event_study.py`.
- `app/backtest/event_study.py`: signal_events × ohlcv_data forward return. **look-ahead 차단**
  (`get_price_on_or_after(event_date+1)`부터). abnormal=종목−벤치(kospi20, 마이그 023). IC/hit/decay.
- 산출 forward-return이 **메타러너 학습 라벨**. lift 사전 임계치 고정 채택 게이트.

### Phase 7 — 학습형 메타러너(L8) + 융합 일원화 ★핵심
- `app/ml/meta_learner.py`를 vol 평균 → **학습형 stacking**으로 일반화: 입력=모든 소스 base 예측/피처
  dict(DART_FIN/OWN/EMP + ALT + vol), 출력=`final_score/direction/confidence(+vol risk)`.
- `MetaCombineTaskHandler`가 ml_inferences뿐 아니라 소스 피처/base score를 수집하도록 입력 확장.
- 출력 스키마: `meta_signals` 확장 또는 신규 fusion 테이블에 direction/score 컬럼. `method` CHECK에
  학습 모델 표기 추가.
- **학습 harness**(`docs/archive/design/meta-learner-training.md` 규율): L6 forward-return 라벨로
  **walk-forward OOF** 학습 → `artifacts/meta_learner.json`(또는 모델 아티팩트). 과적합 가드, leakage 차단.
- **AGGREGATE 재정의**: 결정론 집계 = **cold-start/폴백**(학습 가중·라벨 부족 종목). 학습 메타러너
  가용·lift 입증 시 primary. 점진 전환.
- 회귀: 기존 vol 소비처(recommend/synthesis)가 일반화된 메타러너 출력과 호환되는지.

### Phase 8 — L4 RAG·L5 엔티티 (추가 피처) [L4 pgvector 합의 후]
- L4: report_chunks/pgvector **재도입 필요**(001_baseline에서 제거됨 — 스펙 outdated). 섹션 분해+BGE-M3
  임베딩 → 톤 변화 피처. 미승인 시 텍스트 섹션만(임베딩 보류) 폴백.
- L5: entities/entity_relations + **파싱 LLM(app/llm/clients.py)** 발주처 추출 → 공급망 피처.
- 둘 다 Phase5 피처 레이어에 추가 입력으로 합류(메타러너가 자동 흡수).

## 손대지 않는 것
- 끝단 SYNTHESIZE(생성형 LLM)·RISK_VETO 불변. 기존 수집기/sync/리포/마이그(006/011/013) 재사용.
- 기존 vol 모델(ewma/har/garch/lgbm) 추론은 유지 — 메타러너의 base 입력으로 계속 사용.

## 검증 (end-to-end)
- 피처: 각 소스 피처 as-of 정합성, **PIT 누설 0**(known_at ≤ asof 단위 테스트).
- 라벨: L6 forward return **look-ahead 0**, abnormal/IC/hit 계산 검증.
- 메타러너: **OOF 성능(IC/hit) vs 결정론 베이스라인 lift**, 과적합(train/val gap), cold-start 폴백 동작.
- e2e: 워커 기동 후 `POST /internal/dart/e2e/run`{stock_code:"005930", run_until_idle:true} →
  `final_signals.items[].final_score` 50.00 아닌 차등값, 학습 메타러너 경로/폴백 경로 모두 확인.
- 표준: `migrate.py apply` + `database/tools/check_schema.py`(drift 0), `uv run pytest`(worker·data-access).

## 위험 · 미결정
- **라벨 희소(최대 위험)**: 종목당 DART 이벤트 소수 → 학습셋 작음·과적합. → 전 종목 패널 풀링, 강한
  정규화, 보수적 모델, lift 미입증 시 결정론 폴백 유지. **cold-start 기간(라벨 축적 전) 결정론 필수.**
- **leakage/look-ahead**: 피처 known_at ≤ asof, 라벨 forward만, walk-forward OOF. 생존편향·휴장·상폐 가드.
- **메타러너 의미 변경(vol→일반)**: 기존 vol 소비처(recommend/synthesis) 회귀 영향.
- **표준계정 매핑 사전**(account_mapping 11종 임시): Phase2 전 확정, 미매핑 신호 제외+로깅.
- **pgvector 재도입(L4)**: 운영 영향·팀 합의. **엔티티 정규화(L5)** 난이도 高(corp_code 우선 매칭).
- 대규모·다단계 → **단계별 PR**(Phase 0/1 즉시 → 2~4 → 6 → 7 → 8) 강력 권장.
