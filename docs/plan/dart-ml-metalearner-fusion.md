# DART → 소스별 ML/DL → 학습형 메타러너 합류 계획

> 영역: agent-worker. `docs/plan/ml-metalearner-source-fusion.md`(비-DART 4소스)와 정합되는 DART 편.

## Context
비-DART 4소스(데이터랩·주가·채용·리포트) 융합 계획(`docs/plan/ml-metalearner-source-fusion.md`)에
**DART를 동일 메타러너 인터페이스로 합류**시키는 계획. 핵심 판단:
- **분기보고서(재무)·임직원은 저빈도라 단독 학습이 어렵다** → 메타러너 피처로만.
- **임원·주요주주 지분변동/차입·담보/장내매매 같은 이벤트성 소재는 분석 가능** → DART 이벤트 base ML 모델.

목표: DART 소재를 "이벤트성=base 모델 / 저빈도 정형=메타러너 피처"로 나눠, 같은 학습형 메타러너
(stacking, 공통 타깃=forward return)에 합류시킨다. 결정론 고정숫자 판정·임베딩 없음. 기존 DART 결정론
판정 코드는 삭제, 수집/적재는 재사용.

## DART 소재 분류 (핵심 설계)
| DART 소재 | 적재 테이블 | 빈도 | 이벤트성 | 처리 |
|---|---|---|---|---|
| 지분·내부자(majorstock 대량보유 / elestock 임원·주요주주) | `dart_ownership_events`(011) | 이벤트 드리븐 | ✅ | **이벤트 base ML 모델**(event-study) |
| 주요사항보고서·주요공시 | `signal_events`(DART) | 이벤트 | ✅ | base 모델 피처(event_type/impact) |
| 정형 재무 | `dart_financial_facts`(006) | 분기/연 1~4회 | ❌ 저빈도 | **메타러너 피처**(패널, base 모델 없음) |
| 임직원 | `dart_employee_stats`(013) | 연/반기 | ❌ 저빈도 | **메타러너 피처** |

`dart_ownership_events` 핵심 컬럼: `holder_type`(major/executive/main_shareholder), `shares`, `ratio`,
`shares_delta`, `ratio_delta`, `report_reason`(사유), `report_date`(이벤트일=라벨 기준), `rcept_no`(근거).

## 목표 아키텍처 (메타러너에 합류)
```
[DART 적재] (수집기 기존 — 재사용)
  dart_ownership_events(이벤트)   signal_events(공시 이벤트)
  dart_financial_facts(저빈도)    dart_employee_stats(저빈도)
        | (이벤트 피처 + 저빈도 패널 피처, known_at<=asof PIT)
        v
[DART 이벤트 base ML 모델]  전 종목 패널 풀링, 공통 타깃=forward return
   입력: holder_type, shares_delta, ratio_delta, report_reason 카테고리(장내매수/매도·차입·담보 등),
         공시 event_type/impact     -> ml_inferences(model_name=src_dart)
        v
[학습형 메타러너 stacking] (비-DART 계획과 동일)  src_datalab/src_ohlcv/src_hiring(+Report 피처) + src_dart
   + DART 저빈도 재무/임직원 피처를 메타러너에 직접 투입
   -> [return 채널] final_score/direction/confidence   (가용성 인지 결합: DART 없는 종목/시점 안전)
        ^
[L6 백테스트]  DART 이벤트일(report_date/event_date) 기준 forward return = 학습 라벨
```

## 핵심 원칙 (비-DART 계획과 동일)
1. **DART도 판정하지 않는다 — base 예측/피처만.** 결정론 "고정숫자 verdict" 제거. 결과는 메타러너가.
2. **공통 타깃 = forward return**(L6). DART 이벤트는 자연스러운 event-study(이벤트일 기준).
3. **임베딩/RAG 없음.** 근거 = 공시 참조(rcept_no) + 피처 기여도.
4. **분기보고서 단독 학습 금지**: 재무·임직원은 base 모델 아님, 메타러너 피처로만.

## 결정사항 (비-DART 계획 D1~D4 정합)
- **D1**: DART 이벤트 base 모델도 **전 종목 패널 풀링**. 저빈도(재무·임직원)는 base 모델 없이 메타러너 피처.
- **D2 cold-start**: 과거 공시 백필로 사전학습. DART 이벤트는 과거 데이터 충분 → 가동 시 학습 완료.
- **D3 leakage**: 피처 `known_at ≤ asof`(공시 접수일 rcept/report_date), 라벨 `asof+1` forward, walk-forward OOF.
- **D4**: DART는 **return 채널만** 기여(vol 채널 무관).

## Phase 순서
### Phase 0 — 기존 DART 결정론 판정/스코어링 코드 삭제
삭제: `app/analyzers/dart/{rules,source_result,financials,llm}.py`의 판정·스코어링, `app/agents/dart/*`
LLM 판정 경로, `orchestrator/dart` 집계 판정. **보존**: `app/collectors/dart/*`,
`orchestrator/dart/{ownership,employee,financials}_sync.py`, `repositories/dart_*`(수집·적재·리포).

### Phase 1 — DART 피처 추출 (이벤트 + 저빈도 패널)
- 이벤트 피처(`dart_ownership_events`): holder_type 원-핫, shares_delta/ratio_delta z, **report_reason
  카테고라이저**(장내매수/매도·장외·증여·상속·**주식담보/대차/차입(pledge/loan)**·전환·기타). 담보/차입은
  별도 플래그 피처(잠재 매도압력/유동성 리스크). signal_events event_type/impact 메타.
  ⚠️ report_reason 실제 값/차입·담보 표현은 수집기 파서에서 확인(미지원 사유는 'other').
- 저빈도 패널 피처(`dart_financial_facts`/`dart_employee_stats`): YoY/부채비율/회전율/headcount YoY 등
  순수 계산. known_at=공시 접수일(PIT, D3).

### Phase 2 — L6 이벤트 스터디 라벨 (비-DART 계획 Phase 2와 공유)
DART 이벤트일 기준 forward return(asof+1, abnormal=종목−kospi20). `event_study_panel`·
`app/backtest/event_study.py`를 공유(DART signal_events도 동일 라벨 부착).

### Phase 3 — DART 이벤트 base ML 모델 (전 종목 패널 풀링)
LightGBM/XGBoost, 공통 타깃 forward return, 입력=DART 이벤트 피처. 출력 → `ml_inferences`
(`model_name=src_dart`). 저빈도 재무/임직원은 base 모델 없이 Phase4 메타러너 피처(D1). 정규화·OOF 조기종료.

### Phase 4 — 메타러너 합류 (메타러너 확장)
`MetaCombineTaskHandler` 입력에 `src_dart` 예측 + DART 저빈도 피처 추가. 기존 stacking 메타러너가
비-DART 소스와 함께 융합. **가용성 인지 결합**(`meta_learner.py:79-93`)으로 DART 이벤트 없는 종목/시점
자동 안전. return 채널만 기여(D4).

### Phase 5 — 설명·근거 + e2e
피처 기여도(importance/SHAP) + 공시 근거(rcept_no) 부착. e2e(`/internal/dart/e2e/run` 또는 통합
파이프라인)로 DART 포함 시 final_score 차등·근거 확인.

## 손대지 않는 것 / 의존성
- DART 수집기/적재/sync/리포·마이그(006/011/013) 재사용. 끝단 SYNTHESIZE·RISK_VETO 골격 유지.
- **비-DART 메타러너 계획(`ml-metalearner-source-fusion.md`) 선행/병행**: 본 계획은 그 메타러너에
  합류하는 형태. L6(event_study)·메타러너 일반화·return 채널 스키마는 공유(중복 구현 금지).

## 검증
- 이벤트 피처: report_reason 카테고리 매핑 단위테스트(차입/담보 포함), known_at PIT 누설 0.
- base 모델: 전 종목 패널 OOF 성능(IC/hit), 과적합 가드. 저빈도는 피처로만(모델 미생성 확인).
- 메타러너: src_dart 합류 후 가용성 결합 동작(DART 결측 종목 정상), vol 채널 회귀 0.
- e2e: DART 이벤트 있는 종목 → src_dart 예측 → 메타러너 → final_score 차등 + 공시 근거 부착.
- 표준: `migrate.py apply` + `check_schema.py`(drift 0), `uv run pytest`.

## 위험 · 확인 필요
- **report_reason 표현 미확인**: 차입/담보/대차가 OpenDART 응답·수집기 파서에 실제로 어떻게 오는지
  구현 착수 시 확인(`ownership_api.py`). 미지원이면 원문 카테고라이저+'other' 폴백.
- **DART 이벤트 표본**: 종목·기간당 이벤트 수가 적을 수 있음 → 전 종목 패널 풀링·정규화로 대응(D1).
- 라벨 희소/leakage/cold-start는 비-DART 계획 D1~D3와 동일 규율로 해소.
