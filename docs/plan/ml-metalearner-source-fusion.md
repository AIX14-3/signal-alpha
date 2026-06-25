# 소스별 ML/DL → 학습형 메타러너 stacking 융합 (데이터랩·주가·채용·리포트)

> 영역: agent-worker. 앱 루트: `services/agent-worker/app`. DART는 본 계획 범위 밖(별도 수립).

## Context
현재 소스 신호는 대부분 **결정론 룰 집계(AGGREGATE)** 이고 메타러너(META_COMBINE)는 **변동성 전용**이라,
소스별 결과가 학습 융합되지 않는다.

**원하는 구조(확정):** 팀이 이미 수집·적재한 각 소스 데이터를 → **소스별 각자 맞는 ML/DL 모델**에 넣고 →
그 예측들을 **학습형 메타러너(stacking)** 가 결합해 "이유 있는 결과물"을 낸다.
- 대상 소스(본 계획): **네이버 데이터랩 · 주가(OHLCV) · 채용공고(hiring) · 리포트**.
- **DART는 본 계획에서 제외**(별도 수립). 단 메타러너는 stacking이라, 추후 DART base 예측을
  동일 인터페이스(`ml_inferences` `model_name=src_dart`)로 **그대로 합류 가능**하게 설계한다.
- ❌ 제외: 문서 파싱/RAG/임베딩/벡터 일치율/LLM 토론. 소스별 결정론 "고정숫자 판정" 베이스라인.
- ✅ 포함: 정형 수집데이터 → 소스별 모델 → 메타러너. 근거 = 학습 가중 + 피처 기여도 + 소스 데이터 참조.

## 목표 아키텍처
```
[수집·적재] (팀 구현 완료, 정형 테이블)
  DataLab: datalab_raw_details(search_index 시계열)   OHLCV: ohlcv_data
  Hiring: hiring_search_trend / hiring_signals         Report: report_valuation_facts
        | (소스별 정형 피처 — 임베딩 아님, known_at<=asof PIT)
        v
[소스별 base ML/DL 모델]  공통 타깃(forward return) 예측, 전 종목 패널 풀링
  DataLab_model · OHLCV(기존 vol 모델군) · Hiring_model    (Report=저빈도 -> 메타러너에 피처 직접)
        | (base 예측 -> ml_inferences, model_name=src_*)
        v
[학습형 메타러너 stacking]  L6 forward-return 라벨로 walk-forward OOF 학습, 가용 소스만 재정규화
        -> [return 채널] final_score / direction / confidence   (신규)
        -> [vol 채널]    combined_vol (기존 그대로 — recommend/synthesis 소비)
        v
[RISK_VETO] -> SYNTHESIZE(LLM 설명, 수치 불변) -> 발행      (추후 DART base 동일 경로 합류)
        ^
[L6 백테스트]  forward return = 학습 라벨 + lift 채택 게이트
```

## 그라운딩 사실 (검증 완료)
- **소스 데이터는 이미 정형 적재**: DataLab `datalab_raw_details(search_index, change_pct, is_spike,
  polarity)`, OHLCV `ohlcv_data(o/h/l/c/volume, foreign_net, institution_net)`, Hiring
  `hiring_search_trend(search_index)`/`hiring_signals(relative_strength, is_spike)`, Report
  `report_valuation_facts(target_price, applied/implied_multiple, methodology)`. → 소스별 모델 입력 가능.
- **현재 ML은 OHLCV→vol 뿐**(`app/ml/inference.py`, vol-models). 데이터랩·채용·리포트는 결정론 룰 분석기.
- **스키마 일반적**: `ml_inferences(model_name VARCHAR(50), pred_value DOUBLE, run_key, asof_date,
  horizon)` — 소스별 base 예측 수용 가능. `meta_signals`는 vol 가정(return 채널은 컬럼/테이블 확장).
- **메타러너 합류점**: `app/ml/meta_combine.py`가 `ml_inferences`를 `{model_name: pred}`로 읽어
  `meta_learner.combine`으로 결합. **가용성 인지**(`meta_learner.py:79-93`): 가용 모델만 가중 재정규화
  → 저빈도/결측 as-of 안전, 추후 DART 합류도 동일.

## 핵심 원칙
1. **소스는 판정하지 않는다 — base 예측만 낸다.** 결정론 "고정숫자 verdict" 제거. 결과 산출자는 메타러너뿐.
2. **공통 타깃 = forward return**(L6 라벨). 모든 base가 동일 타깃 예측해야 stacking 성립. vol은 별도 채널.
3. **임베딩/벡터/RAG 없음.** 근거 = 소스 데이터 참조(검색일/공고/리포트 발행일) + 피처 기여도.
4. **결정론 고정숫자 베이스라인 없음.** cold-start는 백필 사전학습으로 제거(D2).

## 결정사항 (위험 해소 — 미결정 없이 확정)
**D1. 라벨 희소·저빈도 대응**
- 모든 base 모델은 **전 종목 패널 풀링**으로 학습(종목별 모델 금지) → 표본 극대화, cross-sectional 일반화.
- base 모델은 **고빈도 소스(DataLab·Hiring·OHLCV)만**. **저빈도 Report는 자체 base 모델 없이 메타러너에
  피처로 직접 투입**(소표본 불안정 모델 회피). 메타러너가 Report 피처 + 타 base 예측을 함께 stacking.
- 강한 정규화(L1/L2·shallow tree·monotonic 제약)·시간분할 OOF 조기종료. lift 미입증 소스는 가중 0 수렴(자연 배제).

**D2. cold-start (결정론 폴백 불채택)**
- **출시 전 과거 데이터 백필로 base/메타러너 사전학습** → 가동 시점에 학습 완료, cold-start 구간 제거.
- 패널 풀링이라 **신규 종목도 즉시 추론**. **피처 최소 윈도우(예: OHLCV 60세션) 미달 종목만 발행 보류**.
- 결정론 폴백 도입하지 않음.

**D3. leakage/look-ahead 고정 규율**
- 피처: `known_at ≤ asof`만(검색일·공고일·리포트 발행일 기준). 라벨: `asof+1` 영업일부터 forward(당일 종가 금지).
- 학습: **walk-forward 시간분할 OOF**(랜덤 split 금지, train은 valid 이전만). 유니버스 스냅샷(생존편향 차단),
  휴장/상폐 가드, 채택 임계치 사전 고정(사후조정 금지).

**D4. 메타러너 타깃 전환 — vol 채널 불변 + return 채널 신규**
- 기존 `combined_vol`(리스크 크기) **그대로 유지** → recommend `vol_weight`·synthesis `ml_risk` 회귀 0.
- **return 융합(final_score/direction/confidence)은 신규 채널**로 추가(meta_signals 컬럼/신규 테이블).
- recommend 랭킹 = `return_score × confidence × vol_weight`(기존 곱셈 구조 유지).

## 메타러너 일반화 (핵심 변경)
- `app/ml/meta_learner.combine`: vol 등가평균 → **학습형 stacking**(입력=소스 base 예측 dict + 저빈도
  소스 피처, 출력=return 채널 final_score/direction/confidence). vol 채널은 기존 경로 그대로 병행.
- `MetaCombineTaskHandler`가 소스별 base 예측을 한 run_key 아래 `model_name=src_datalab/src_ohlcv/
  src_hiring`(+Report 피처, +추후 src_dart)로 모아 stacking.
- 학습 harness(`docs/archive/design/meta-learner-training.md` 규율): L6 라벨 walk-forward OOF → 아티팩트.

## "이유 있는 결과물" (설명·근거)
메타러너 학습 가중 = 각 소스가 실제 수익률을 얼마나 설명하는지(데이터 근거). 결과별 설명 = **피처 기여도**
(LightGBM importance / SHAP) + **소스 근거 참조**(어느 검색어/공고/리포트가 기여). 벡터 유사도 아님.

## Phase 순서
의존성: 소스 피처(이미 적재) + **L6 라벨 → base 모델 + 메타러너 학습**. L6 선결. 단계별 PR.

### Phase 0 — 대상 소스의 결정론 판정 역할 제거(피처추출로 전환)
데이터랩·채용·리포트 분석기(`analyzers/{datalab,hiring,report}/*`)의 고정숫자 판정/스코어링 제거, **피처
산출만** 보존. 결정론 소스 집계(AGGREGATE) 점수 판정 역할 제거(순수 배관 or 폐기). OHLCV는 ML 유지.

### Phase 1 — 소스별 피처 어셈블리 (정형, as-of/PIT)
`get_features(stock, asof)` 피처 스토어(또는 contract 확장), `known_at ≤ asof`(D3). DataLab: search_index
MA/모멘텀/spike/polarity. Hiring: relative_strength MA/spike/섹터 상대강도. Report: target gap/multiple
gap/methodology·broker consensus. OHLCV: 기존 vol 피처.

### Phase 2 — L6 백테스트(라벨 + 채택 게이트) ★선결
마이그 `event_study_panel`(signal_event_id FK, fwd_return_1/5/20d, abnormal_return_20d). `app/backtest/
event_study.py`: forward return(D3 look-ahead 차단, abnormal=종목−kospi20). → 학습 라벨 + lift 채택 게이트.

### Phase 3 — 소스별 base ML/DL 모델 (전 종목 패널 풀링)
고빈도 소스(DataLab/Hiring) base 모델(권장 LightGBM/XGBoost, 공통 타깃=forward return), OHLCV는 기존
vol 모델군 유지. 출력 → `ml_inferences`(run_key 공유, `model_name=src_*`). Report는 base 모델 없이
Phase4 메타러너 피처로(D1). 정규화·OOF 조기종료.

### Phase 4 — 학습형 메타러너 stacking (융합 일원화) ★핵심
`meta_learner.combine` 일반화 + `MetaCombineTaskHandler` 입력 확장(base 예측 + Report 피처) + **return
채널 출력 스키마**(direction/score) 확장. L6 라벨 OOF 학습. `final_signals`에 return 메타러너 결과 반영.
**vol 채널은 기존 그대로 병행**(D4). 소스 추가에 열린 인터페이스(추후 DART).

### Phase 5 — 설명·근거 + e2e 배선
피처 기여도/소스 근거 부착, 전 소스 파이프라인 배선, recommend = return_score×confidence×vol_weight
회귀 확인(D4).

## 손대지 않는 것 / 범위 밖
- 팀 구현 소스 **수집기/적재 테이블** 재사용. 끝단 SYNTHESIZE·RISK_VETO 골격 유지. OHLCV vol 모델군 유지.
- **DART는 범위 밖**(별도 수립). 메타러너 인터페이스만 DART 합류에 열어둔다.

## 검증
- 피처: as-of PIT 누설 0 단위테스트(D3). L6: look-ahead 0, abnormal/IC/hit 검증.
- 메타러너: **OOF 성능(IC/hit/lift)**, 과적합(train/val gap), 가용성 결합(소스 결측 시) 동작, vol 채널 회귀 0.
- e2e: 종목 → 3 base(+Report 피처) → ml_inferences(src_*) → 메타러너 → final_score 차등값 + 설명/근거.
- 표준: `migrate.py apply` + `check_schema.py`(drift 0), `uv run pytest`(worker·data-access).

## 수용한 잔여 리스크 (모니터링 대상, 차단 아님)
- 크롤러 안정성(hiring 포털 변경)·Report 추출 품질(needs_review)은 데이터 품질 모니터로 관리.
- 메타러너 lift가 결정론 베이스라인 대비 우위를 못 내면(가능성) → 해당 소스 가중 0 수렴으로 자연 배제,
  L6 채택 게이트가 차단. (베이스라인 없는 정책상, 전 소스 lift 미달 시 발행 보류.)
