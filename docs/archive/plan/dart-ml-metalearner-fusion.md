# DART → 소스별 ML/DL → 학습형 메타러너 합류 계획

> ⚠️ **#11 업데이트**: DART/REPORT 판정 경로는 LLM/결정론으로 변경됨(메타러너 미사용). 워커는 큐 드레인 데몬으로 발행까지 연속 소비. 토폴로지는 [architecture-diagram.md](../../architecture-diagram.md) 참조. 아래 `src_dart` 메타러너 합류(SRC_INFER return 채널)는 코드만 있고 라이브 미배선/dormant — 운영 경로에서 DART는 LLM 정제(+RISK_VETO 결정론 룰)로 끝단 SYNTHESIZE에 근거로만 합류한다.

> 영역: agent-worker (+ 수집 DB). 상위 통합 계획 `docs/plan/db-split-and-ml-metalearner-fusion.md`
> (#525+#531)의 **DART 편**. 그 계획 §8이 예약해 둔 `ml_inferences` `model_name=src_dart`
> 인터페이스에 DART를 끼운다. 문서 전용(이 문서는 구현 계획이며, 코드 변경은 후속 PR로).

## 1. 배경 / 정합

상위 통합 계획 `db-split-and-ml-metalearner-fusion.md` §8(손대지 않는 것 / 범위 밖)은 DART를
**명시적으로 범위 밖**으로 두되 합류 인터페이스만 예약한다:

> "DART 융합은 범위 밖(별도 수립). 메타러너 인터페이스(`ml_inferences` `model_name=src_dart`)만
> 추후 합류 가능하게 열어둔다."

본 문서가 그 예약된 합류 편이다. 비-DART 4소스(데이터랩·주가·채용·리포트) 융합은 main에
이미 착지했고(WS-B base 모델 + WS-C return 채널), DART는 **같은 표면에 `src_dart`로 더해지는**
형태다. 핵심 판단:

- **분기보고서(재무)·임직원은 저빈도라 단독 학습이 어렵다** → base 모델 없이 메타러너 피처로만(D1).
- **임원·주요주주 지분변동/차입·담보/장내매매 같은 이벤트성 소재는 분석 가능** → DART 이벤트
  base ML 모델(event-study).

목표: DART 소재를 "이벤트성=base 모델 / 저빈도 정형=메타러너 피처"로 나눠, 같은 학습형 메타러너
(stacking, 공통 타깃=forward return)에 합류시킨다. 결정론 고정숫자 판정·임베딩 없음. 기존 DART
결정론 판정 코드는 삭제, 수집·정규화·적재는 재사용.

상위 계획의 결정 **D1~D5를 그대로 적용**한다(전 종목 패널 풀링 / cold-start 백필 / leakage
walk-forward OOF / vol 채널 불변+return 신규 / 근거=피처 기여도+공시 참조).

## 2. main 토폴로지 정합 (먼저 읽기)

main은 단일 DB가 아니라 **2-인스턴스 물리 분리**(수집 DB / 백엔드 DB) + 앱레벨 publish다
(`docs/architecture-diagram.md`, `db-split-and-ml-metalearner-fusion.md` §2·§3). DART 합류가
닿는 테이블 위치:

| 테이블 | DB | 정의 | 비고 |
|---|---|---|---|
| `dart_ownership_events` · `dart_financial_facts` · `dart_employee_stats` | **수집 DB** | `database/migrations/0003_collection_baseline.sql` | 레거시 마이그 011/006/013은 `database/migrations/archive/`로 이동(재베이스라인 #531) |
| `signal_events` | **PUBLISHED**(양쪽) | `0002_published_baseline.sql` | DART 공시 이벤트 적재 대상 |
| `ml_inferences` · `meta_signals` · `event_study_panel` | **수집 DB** | `0003_collection_baseline.sql` | base 예측·메타결합·라벨 패널 |
| `final_signals` | **PUBLISHED**(양쪽) | `0002_published_baseline.sql` | return 채널 컬럼 `ml_final_score/ml_direction/ml_confidence` 보유 |

→ DART base 예측·메타결합은 **모두 수집 DB**에서 일어나고, return 채널 결과만 기존 publish 경로로
백엔드 DB에 발행된다. DART는 **publish 표면(publisher·백엔드 read-model)을 새로 만들지 않는다**
— WS-C가 이미 공동 설계한 표면에 `src_dart`로만 합류(두 번 손대지 않음).

## 3. DART 소재 분류 (핵심 설계)

| DART 소재 | 적재 테이블(수집 DB) | 빈도 | 이벤트성 | 처리 |
|---|---|---|---|---|
| 지분·내부자(majorstock 대량보유 / elestock 임원·주요주주) | `dart_ownership_events` | 이벤트 드리븐 | ✅ | **이벤트 base ML 모델**(event-study, `model_name=src_dart`) |
| 주요사항보고서·주요공시 | `signal_events`(source_type=DART) | 이벤트 | ✅ | base 모델 피처(event_type/impact 메타) |
| 정형 재무 | `dart_financial_facts` | 분기/연 1~4회 | ❌ 저빈도 | **메타러너 피처**(패널, base 모델 없음) |
| 임직원 | `dart_employee_stats` | 연/반기 | ❌ 저빈도 | **메타러너 피처** |

`dart_ownership_events` 실제 컬럼(`0003_collection_baseline.sql:365-382`):
`holder_type`(major/executive/main_shareholder), `shares`, `ratio`, `shares_delta`, `ratio_delta`,
`report_reason`(사유, varchar(100)), `report_date`(이벤트일=known_at), `rcept_no`(근거).

## 4. 목표 아키텍처 (메타러너에 합류, 2-DB 정합)

```
[DART 적재] (수집기 기존 — 재사용)                      ┌── 수집 DB (collection) ──────────────┐
  dart_ownership_events(이벤트)  signal_events(공시)     │ dart_* · signal_events · ml_inferences │
  dart_financial_facts(저빈도)   dart_employee_stats     │ meta_signals · event_study_panel       │
        | (이벤트 피처 + 저빈도 패널 피처, known_at<=asof PIT, D3)                                 │
        v                                                                                          │
[DART 이벤트 base ML 모델]  전 종목 패널 풀링, 공통 타깃=forward return                            │
   입력: holder_type, shares_delta, ratio_delta, report_reason 카테고리(장내매수/매도·차입·담보),  │
         signal_events event_type/impact                                                           │
        | -> ml_inferences(run_key='SRC', model_name='src_dart', horizon=20)                       │
        v                                                                                          │
[학습형 메타러너 stacking]  combine_return(ReturnCombineTaskHandler, run_key='SRC')                │
   base 예측: src_datalab/src_hiring/src_dart  +  메타러너 피처: Report·DART 저빈도(재무·임직원)    │
   -> [return 채널] meta_signals.final_score/direction + confidence (가용성 인지 결합: DART 결측 안전)│
   -> [vol 채널]    combined_vol (run_key='ML', 불변 — D4)                                          │
        |                                                                                          │
        v  final_signals.ml_final_score/ml_direction/ml_confidence 오버레이                        │
[앱레벨 publish] ──────────────────────────────────────────────────────────────┐                 │
        ^                                                                        │ publish         │
[L6 백테스트]  DART 이벤트일(report_date) 기준 forward return = 학습 라벨        └─────────────────┘
                                                                                 v
                                          ┌── 백엔드 DB (backend) ── final_signals(발행분) → main-server → web
```

## 5. 핵심 원칙 (비-DART 계획과 동일)

1. **DART도 판정하지 않는다 — base 예측/피처만.** 결정론 "고정숫자 verdict" 제거. 판정은 메타러너가.
2. **공통 타깃 = forward return**(L6). DART 이벤트는 자연스러운 event-study(이벤트일 기준).
3. **임베딩/RAG 없음.** 근거 = 공시 참조(`rcept_no`) + 피처 기여도(importance/SHAP).
4. **분기보고서 단독 학습 금지**: 재무·임직원은 base 모델 아님, 메타러너 피처로만(D1).
5. **vol 채널 불변(D4)**: DART는 **return 채널만** 기여. `combined_vol`(run_key='ML')은 한 줄도 안 건드림.

## 6. Phase 순서 (main 워크스트림 매핑)

> DART의 각 Phase가 main의 어느 표면에 꼽히는지: Phase 0~1·3 = **WS-B**(ML 내부, 수집 DB),
> Phase 4 = **WS-C**(return 수렴), Phase 2 = WS-B/WS-C 공유 L6 라벨(이미 구현).

### Phase 0 — 기존 DART 결정론 판정/스코어링 코드 삭제 (WS-B)
- 삭제: `app/analyzers/dart/{rules,source_result,llm}.py`의 판정·스코어링, `app/agents/dart/*`
  LLM 판정 경로, `orchestrator/dart` 집계 판정.
- **보존**: `app/collectors/dart/*`(`ownership_api.py` 포함), `orchestrator/dart/{ownership,employee,
  financials}_sync.py`, `repositories/dart_*`(수집·정규화·적재·리포).
- 비-DART Phase 0(commit `ad95462`)이 확립한 패턴 따름: 각 소스 `direction="unknown"` +
  `data_status="no_signal"` 반환 → AGGREGATE 점수·방향 집계에서 자연 제외(기존 no_signal/unknown
  제외 로직 재사용). 전환기 동안 DART는 판정 안 냄 — 메타러너 return 채널이 산출.

### Phase 1 — DART 피처 추출 (이벤트 + 저빈도 패널) (WS-B)
- 이벤트 피처(`dart_ownership_events`): `holder_type` 원-핫, `shares_delta`/`ratio_delta` z,
  **`report_reason` 카테고라이저**(장내매수/매도·장외·증여·상속·**주식담보/대차/차입(pledge/loan)**·
  전환·기타). 담보/차입은 별도 플래그 피처(잠재 매도압력/유동성 리스크). `signal_events`의
  event_type/impact 메타.
  ⚠️ `report_reason` 실제 값·차입/담보 표현은 수집기 파서(`app/collectors/dart/ownership_api.py`)에서
  확인 — 미지원 사유는 `'other'` 폴백.
- 저빈도 패널 피처(`dart_financial_facts`/`dart_employee_stats`): YoY/부채비율/회전율/headcount YoY 등
  순수 계산. known_at=공시 접수일(PIT, D3).
- 구현 지점: `app/ml/source_features.py`. `assemble_features`에 **dart 브랜치 추가**(`{"datalab":...,
  "hiring":..., "report":..., "dart":...}` 반환), `KNOWN_AT`에 dart known_at 키 추가
  (`report_date` 또는 rcept 접수일). 기존 `pit_rows` 게이트(`known_at <= asof`)와 `_numeric`
  평탄화를 그대로 재사용 — 판정 아닌 순수 피처만.

### Phase 2 — L6 이벤트 스터디 라벨 (이미 구현, 공유)
- **이미 main에 있음**: `app/backtest/event_study.py`(`forward_returns_from_entry` L68-94,
  `compute_abnormal_return`, lift 채택 게이트 `compute_lift`), `event_study_panel`(수집 DB).
  D3 look-ahead 차단(이벤트일 다음 영업일 진입, 당일 종가 제외)도 구현됨.
- DART 추가분: DART `signal_events`/이벤트일(`report_date`)을 **동일 패널에 라벨 부착**만 한다
  (forward return = `asof+1`부터, abnormal = 종목−벤치). 새 라벨 로직 작성 금지(공유).

### Phase 3 — DART 이벤트 base ML 모델 (전 종목 패널 풀링) (WS-B)
- LightGBM/XGBoost, 공통 타깃 forward return, 입력=DART 이벤트 피처. 출력 → `ml_inferences`
  (`run_key='SRC'`, `model_name='src_dart'`, `horizon=20`).
- 구현 지점:
  - `app/ml/source_models.py`: `SOURCE_MODELS`에 `"src_dart": "dart"` 추가. `feature_order("dart")`는
    `assemble_features(_REF_DATE)["dart"]`에서 자동 파생(Phase 1의 dart 브랜치 전제).
  - `app/ml/source_inference.py` `SrcInferTaskHandler`: dart 행 입력 추가, `predict_sources`에 dart
    경로 합류 → 예측을 `run_key=SOURCE_RUN_KEY('SRC')`로 upsert. `SOURCE_RUN_KEY`는
    `source_models.py:35`(="SRC", vol 결합 run_key='ML'과 분리해 `combined_vol` 오염 차단, D4).
  - `app/ml/source_training.py`/`train_source_models.py`: dart 소스 학습 추가. 규율 =
    `docs/archive/design/meta-learner-training.md`(walk-forward OOF, 정규화·조기종료, train↔OOF 격차 점검).
- 저빈도 재무/임직원은 base 모델 없이 Phase 4 메타러너 피처(D1). lightgbm 미설치/아티팩트 부재 =
  `predict` None(결측), 결정론 폴백 없음(D2) — 메타러너 가용성 재정규화가 흡수.

### Phase 4 — 메타러너 합류 (return 채널 확장) (WS-C)
- 합류 핸들러는 **`ReturnCombineTaskHandler`**(`app/ml/return_combine.py`) — vol 채널
  `MetaCombineTaskHandler`(run_key='ML')가 **아니다**. 이 핸들러는 `ml_inferences.list_for_run(
  run_key='SRC')`를 읽어 `model_name in SOURCE_MODELS`만 base 예측으로 모은다(`return_combine.py:66-78`)
  → `SOURCE_MODELS`에 `src_dart`가 추가되면 자동 합류.
- `combine_return`(`app/ml/meta_learner.py` L164-214)이 base 예측 dict + Report 피처(+DART 저빈도
  피처)를 stacking. **가용성 인지 재정규화**(L191-205)로 DART 이벤트 없는 종목/시점은 입력 항에서
  자동 제외 → 결측 안전.
- DART 저빈도(재무·임직원) 피처는 `ReturnCombineTaskHandler._report_features`와 같은 방식으로
  PIT 어셈블해 `combine_return(report_features=...)` 경로에 합류(`report__` 프리픽스 관례 따라
  `dart__` 등으로 네임스페이스 분리 검토).
- return 채널만 기여(D4): `combined_vol=None` 유지(`return_combine.py:92`). 결과는
  `meta_signals(run_key='SRC').final_score/direction` + `final_signals.ml_final_score/ml_direction/
  ml_confidence` 오버레이(`analysis.update_final_signal_return_channel`, `return_combine.py:102-107`)로
  기존 publish 경로에 자동 전파.

### Phase 5 — 설명·근거 + e2e (WS-C)
- 피처 기여도(importance/SHAP) + 공시 근거(`rcept_no`) 부착(D5). 임베딩/RAG 아님.
- e2e: DART 이벤트 있는 종목 → `src_dart` 예측 → 메타러너 → `final_score` 차등 + 공시 근거 확인.
  publish 후 백엔드 DB `final_signals` return 채널 반영·프론트 소비 확인.

## 7. 검증

- 이벤트 피처: `report_reason` 카테고리 매핑 단위테스트(차입/담보 포함), known_at PIT 누설 0(D3).
- base 모델: 전 종목 패널 OOF 성능(IC/hit), 과적합 가드. 저빈도는 피처로만(모델 미생성 확인).
- 적재: base 예측이 `ml_inferences(run_key='SRC', model_name='src_dart', horizon=20)`에 적재.
- 메타러너: `src_dart` 합류 후 가용성 결합 동작(DART 결측 종목 정상), **vol 채널 회귀 0**
  (`combined_vol` 불변, run_key='ML' 무손상).
- e2e: DART 포함 시 `final_signals.ml_final_score` 차등 + 공시 근거 부착, publish 후 백엔드 반영.
- 표준: `python database/migrate.py apply --target collection` + `check_schema.py`(drift 0),
  `uv run pytest`(worker·data-access).

## 8. 손대지 않는 것 / 의존성

- DART 수집기/정규화/sync/리포·테이블(`dart_ownership_events`/`dart_financial_facts`/
  `dart_employee_stats`, 수집 DB) 재사용. 끝단 SYNTHESIZE·RISK_VETO 골격 유지.
- **publish 표면(publisher·백엔드 read-model·발행 테이블 형상)은 WS-C가 이미 공동 설계** — DART는
  `model_name=src_dart`로만 합류, 발행 계약·백엔드 read-model을 새로 손대지 않는다.
- 비-DART 메타러너 융합(`db-split-and-ml-metalearner-fusion.md` WS-B/WS-C)이 선행/병행 전제. L6
  (`event_study`)·`combine_return`·return 채널 스키마는 공유(중복 구현 금지).
- 상세 수집/적재 스펙은 `docs/spec/dart-collector-analyzer-spec.md`(§5·§6 유효),
  `docs/spec/dart-l1-financials-spec.md` 참조. (그 문서들의 rule 기반 분석 판정 절은 Phase 0로 폐기.)

## 9. 위험 · 확인 필요 (모니터링, 차단 아님)

- **report_reason 표현 미확인**: 차입/담보/대차가 OpenDART 응답·수집기 파서
  (`app/collectors/dart/ownership_api.py`)에 실제로 어떻게 오는지 구현 착수 시 확인. 미지원이면
  원문 카테고라이저 + `'other'` 폴백.
- **DART 이벤트 표본 희소**: 종목·기간당 이벤트 수가 적을 수 있음 → 전 종목 패널 풀링·정규화로 대응(D1).
  lift 미입증 소스는 가중 0 수렴으로 자연 배제(L6 채택 게이트).
- 라벨 희소/leakage/cold-start는 비-DART 계획 D1~D3와 동일 규율로 해소.
