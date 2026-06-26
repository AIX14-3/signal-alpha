# 통합 계획: DB 2-인스턴스 분리 + 소스별 ML/DL 메타러너 융합 (#525 + #531)

> 상태: 제안(Proposal). 두 작업(#525 메타러너 융합, #531 DB 분리)을 **하나의 계획·단일 브랜치**로 통합한다.
> 구현은 워크스트림별 단계 커밋으로 진행한다. 영역: agent-worker + main-server + database + web.
> 이 문서는 기존 `docs/plan/ml-metalearner-source-fusion.md`(#525)와 `docs/db-split-plan` 브랜치의
> db-split 문서(#531)를 **흡수·대체**한다.

## 1. 배경 (왜 합치나)

두 작업이 **별도 브랜치로 병렬 진행**되면서 같은 표면을 양쪽에서 두 번 수정·재작업해야 한다:
`final_signals`, `database/migrate.py`, `services/*/app/core/config.py`,
`packages/data-access/.../database.py`, 백엔드 read-model. 충돌이 잦고 비효율적이다.

- **#525 — 소스별 ML/DL → 학습형 메타러너 stacking 융합.** 현재 소스 신호는 결정론 룰 집계(AGGREGATE)이고
  메타러너(META_COMBINE)는 변동성 전용이라 소스별 결과가 학습 융합되지 않는다. 각 소스 정형 데이터를
  소스별 ML/DL 모델에 넣고, 예측을 학습형 메타러너가 stacking해 **return 채널**
  (`final_score/direction/confidence`)을 신설, `final_signals`에 반영한다.
- **#531 — DB 2-인스턴스 물리 분리 + 프론트엔드 갭 마이그레이션.** 현재는 단일 Postgres `public` 스키마에
  워커 산출물과 백엔드 서비스 테이블이 섞여 있고, 분리는 `api.*` 읽기전용 view + 2개 롤로 **논리적**일 뿐이다.
  ① 수집 DB / 백엔드 DB **물리 분리**, ② 회원·결제·구독일·감사 등 프론트엔드→DB 갭 해소가 목표.

**충돌의 본질 (왜 한 번에):** 현재 백엔드는 워커 산출물을 `api.*` view로 읽는다
(`api.signals_current = final_signals.* + stocks + analysis_results`,
`database/migrations/20260625_1343_api_schema_read_contract.sql`). #531 Group B는 물리 분리 후
cross-DB JOIN이 불가하므로 이 view를 **삭제**하고 워커가 산출물을 백엔드 DB로 기록하는 **앱레벨 publisher**로
대체한다. 바로 그 표면에 #525가 프론트엔드가 소비할 **return 채널 컬럼**을 추가한다.
→ **publish 계약 = 메타러너 return 출력 스키마 = 동일 표면.** 따로 만들면 publisher·백엔드 read-model·
프론트엔드를 두 번 손대게 된다. 그래서 **수렴점(WS-C)을 한 번에 공동 설계**한다.

## 2. 확정된 설계 결정

### DB 분리 (#531)
- 별도 Postgres **인스턴스 2개** (단일 인스턴스 내 2 DB 아님).
- 워커 산출물은 **앱레벨 발행(publish)** 으로 백엔드 DB에 기록 — 물리 분리 시 cross-DB FK/JOIN 불가.
- **그린필드** — 기존 단일 DB 백필 없음, 신규 스키마로 부트스트랩.
- 이력은 **신규 테이블 추가** 로 모델링.

### 메타러너 융합 (#525)
- **D1. 라벨 희소·저빈도** — 모든 base 모델은 **전 종목 패널 풀링**으로 학습(종목별 모델 금지). base 모델은
  고빈도 소스(DataLab·Hiring·OHLCV)만, **저빈도 Report는 base 없이 메타러너에 피처로 직접 투입**. 강한
  정규화(L1/L2·shallow tree·monotonic)·시간분할 OOF 조기종료. lift 미입증 소스는 가중 0 수렴(자연 배제).
- **D2. cold-start** — 출시 전 과거 데이터 백필로 base/메타러너 사전학습. 패널 풀링이라 신규 종목도 즉시
  추론, 피처 최소 윈도우(예: OHLCV 60세션) 미달만 발행 보류. 결정론 폴백 불채택.
- **D3. leakage/look-ahead** — 피처 `known_at ≤ asof`(검색일·공고일·리포트 발행일), 라벨은 `asof+1`
  영업일부터 forward(당일 종가 금지). 학습은 **walk-forward 시간분할 OOF**(랜덤 split 금지), 유니버스
  스냅샷(생존편향 차단), 채택 임계치 사전 고정.
- **D4. vol 채널 불변 + return 채널 신규** — 기존 `combined_vol`(리스크 크기) 그대로 유지(recommend
  `vol_weight`·synthesis `ml_risk` 회귀 0). return 융합(`final_score/direction/confidence`)은 신규 채널.
  recommend 랭킹 = `return_score × confidence × vol_weight`(기존 곱셈 구조 유지).
- **D5. 근거** — 피처 기여도(LightGBM importance / SHAP) + 소스 데이터 참조(검색어/공고/리포트). 임베딩/
  벡터/RAG 아님.

## 3. 목표 아키텍처

```
[수집·적재] (정형 테이블, 팀 구현 완료)                      ┌── 수집 DB (collection) ──┐
  DataLab: datalab_raw_details   OHLCV: ohlcv_data            │ 워커 테이블 + ml_inferences │
  Hiring: hiring_search_trend    Report: report_valuation     │ meta_signals · event_study │
        | (소스별 정형 피처, known_at<=asof PIT)                └────────────┬──────────────┘
        v                                                                   │ 앱레벨 publish
[소스별 base ML/DL]  공통 타깃 forward return, 전 종목 패널 풀링            │ (cross-DB JOIN 불가)
  DataLab · OHLCV(vol군) · Hiring   (Report=피처 직접)                       v
        | (base 예측 -> ml_inferences, model_name=src_*)        ┌── 백엔드 DB (backend) ───┐
        v                                                       │ users·admin·결제·구독     │
[학습형 메타러너 stacking]  L6 라벨 walk-forward OOF             │ + 발행 산출물 테이블       │
  -> [return 채널] final_score/direction/confidence (신규) ─────│   (api.* view 대체)        │
  -> [vol 채널]    combined_vol (기존)                           └────────────┬──────────────┘
        v                                                                    │ SELECT
[RISK_VETO] -> SYNTHESIZE(LLM 설명, 수치 불변) -> 발행                       v
        ^                                                          main-server -> web(프론트)
[L6 백테스트]  forward return = 학습 라벨 + lift 채택 게이트
```

## 4. 이미 완료된 베이스라인 (현재 브랜치 `feat/db-split-billing-migrations`)

- **#531 Group A** — 결제/환불 append-only 이력(`payments`), 구독 결제일(`next_billing_at`/`auto_renew`),
  회원상태(`users.status`), 관리자 감사로그(`admin_audit_log`) + CRUD 배선 (`d5bf1bf`). 백엔드 소유
  테이블이라 단일 DB·2-DB 양쪽 호환 → 그대로 둔다.
  - 마이그: `database/migrations/20260626_141{0,1,2}_*.sql`
  - 코드: `services/main-server/app/api/routes/{admin,payments}.py`,
    `packages/data-access/.../repositories/{admin,users_billing}.py`
- **#525 Phase 2** — L6 event-study forward-return 라벨 패널 + lift 채택 게이트 (`faf3488`).
  `app/backtest/event_study.py`, 마이그 `..._event_study_panel_forward_return_labels.sql`.

## 5. 작업: 4개 워크스트림 (단일 브랜치)

> 의존성: **WS-A(토폴로지) ∥ WS-B(ML 내부)** 독립 병렬 → 둘 다 착지 후 **WS-C(수렴)** → **WS-D(문서)**.

### WS-A — DB 토폴로지 (#531 Group B). 수집 DB / 백엔드 DB 분리
- **마이그레이션 러너 대상 DB 선택**(collection/backend) 도입 — `database/migrate.py`,
  `database/check_schema.py`.
- **설정 분리**: 워커 `services/agent-worker/app/core/config.py`(수집 DB DSN + 발행용
  `BACKEND_DATABASE_URL`), 메인서버 `services/main-server/app/core/config.py`(백엔드 DB DSN).
  `packages/data-access/signal_alpha_data_access/database.py`가 **두 풀** 생성.
- **엔진/세션 배선**: `services/agent-worker/app/core/database.py`,
  `services/main-server/app/core/database.py`.
- **스키마 2분할**: `database/schema.sql` → 수집 DB(워커 테이블) / 백엔드 DB(회원·관리자·결제 + 발행
  산출물 테이블). **그린필드** 부트스트랩 스크립트(백필 없음).

### WS-B — ML 내부 (#525 Phase 0·1·3). 수집 DB 한정, 토폴로지 무관
- **Phase 0 — 결정론 판정 제거**: `analyzers/{datalab,hiring,report}/*`의 고정숫자 verdict/스코어링 제거,
  **피처 산출만** 보존. 결정론 소스 집계(AGGREGATE) 점수 판정 역할 제거. OHLCV는 ML 유지.
- **Phase 1 — 소스 피처 어셈블리**: `get_features(stock, asof)` 피처 스토어(또는 contract 확장),
  `known_at ≤ asof`(D3). DataLab(search_index MA/모멘텀/spike/polarity) · Hiring(relative_strength
  MA/spike/섹터 상대강도) · Report(target/multiple gap·methodology·broker consensus) · OHLCV(기존 vol 피처).
- **Phase 3 — 소스별 base 모델**: 고빈도 소스(DataLab/Hiring) LightGBM/XGBoost, 공통 타깃=forward
  return, 전 종목 패널 풀링 → `ml_inferences`(run_key 공유, `model_name=src_datalab/src_hiring`). OHLCV는
  기존 vol 모델군 유지. Report는 base 없이 메타러너 피처(D1). 정규화·OOF 조기종료. 출력 테이블 = **수집 DB**.

### WS-C — 수렴점 (★공동 설계: #525 Phase 4·5 ∩ #531 publisher)
**한 번에 정의하는 publish 계약 = 메타러너 return 출력 스키마.**
- **메타러너 일반화**: `services/agent-worker/app/ml/meta_learner.py`의 `combine`을 등가평균 → 학습형
  stacking으로(입력=base 예측 dict + Report 피처, 출력=return 채널). `MetaCombineTaskHandler`
  (`app/ml/meta_combine.py`) 입력을 base 예측(src_*) + Report 피처로 확장. 가용 소스만 재정규화(결측 안전).
  vol 채널(`combined_vol`)은 기존 경로 그대로 병행(D4). 학습 harness는
  `docs/archive/design/meta-learner-training.md` 규율(L6 라벨 walk-forward OOF → 아티팩트).
- **출력 스키마 1회 정의**: `final_signals`(+`meta_signals`)에 return 채널 컬럼(`final_score`,
  `direction`, `confidence`) 추가. **백엔드 DB 발행 산출물 테이블 형상 = 오늘의 `api.signals_current`
  형상 + return 채널 컬럼** — 처음부터 함께 설계(두 번 손대지 않음).
- **publisher 신설(api.* view 대체)**: 워커가 META_COMBINE 산출(=return 채널 포함 final_signals + 필요
  join 필드)을 백엔드 DB 발행 테이블에 기록(앱레벨 publisher). 기존
  `20260625_1343_api_schema_read_contract.sql` / `..._db_roles_and_grants.sql`는 컷오버 시 재설계/제거.
- **백엔드 read-model 전환**: `services/main-server/app/api/routes/{signals,dashboard}.py` 등 소비처를
  `api.*` view → 백엔드 DB 발행 테이블로 1회 전환.
- **마이그레이션 대상 배치**: `event_study_panel`·`ml_inferences`·`meta_signals` → **수집 DB**, 발행 시그널
  테이블 → **백엔드 DB**.
- **Phase 5 e2e/설명**: 피처 기여도(LightGBM importance/SHAP)+소스 근거 부착, 전 소스 파이프라인 배선,
  recommend = `return_score × confidence × vol_weight` 회귀 확인(D4).

### WS-D — 문서 (#531 Group C + #525 설명)
- `docs/architecture-diagram.md`: 2-DB 토폴로지 + publish 흐름 + 메타러너 return 채널.
- `docs/runbooks/pre-deploy-staging-rehearsal-runbook.md`(신규): 2-인스턴스 provisioning/마이그레이션 단계.
- 본 통합 문서 최종화.

## 6. 롤아웃 순서 (단일 브랜치, 단계별 커밋)

1. **WS-A ∥ WS-B** — 토폴로지(설정/스키마)와 ML 내부(수집-DB만)는 서로 독립. 병렬 진행.
2. **WS-C** — WS-A의 dual-pool + WS-B의 base 예측이 모두 있어야 함. 수렴점을 한 번에 공동 설계해 착지.
3. **WS-D** — WS-C와 함께/직후.

> 핵심: 발행 테이블·publisher·백엔드 read-model·프론트엔드를 **WS-C에서 한 번만** 손댄다(return 채널
> 포함). WS-A를 오늘 형상으로 먼저 굳히고 나중에 #525가 컬럼을 더하는 재작업을 피한다.

## 7. 검증

- **표준**: `python database/migrate.py apply`(collection/backend 각각) + `check_schema.py` drift 0,
  `uv run pytest`(worker·data-access·main-server).
- **WS-A**: 두 DB에 마이그레이션 적용(로컬: 인스턴스 2개 또는 동일 인스턴스 2 DB 리허설). 백엔드가 수집 DB
  직접 의존 안 함.
- **WS-B**: as-of PIT 누설 0 단위테스트(D3). base 예측이 `ml_inferences`(src_*)에 적재.
- **WS-C(수렴)**: 종목 → 3 base(+Report 피처) → `ml_inferences(src_*)` → 메타러너 → return 채널
  차등값 + 설명/근거. **워커 발행 후 백엔드 DB 발행 테이블에 행 생성**, 프론트엔드가 return 채널 소비.
  vol 채널 회귀 0. OOF 성능(IC/hit/lift)·과적합(train/val gap)·가용성 결합(소스 결측) 동작.
- **WS-D**: 다이어그램/런북이 코드와 일치.

## 8. 손대지 않는 것 / 범위 밖

- 이미 완료된 #531 Group A(결제/감사) · #525 L6 Phase 2 라벨.
- 팀 구현 소스 수집기/적재 테이블 재사용, 끝단 SYNTHESIZE·RISK_VETO 골격, OHLCV vol 모델군.
- 문서 파싱/RAG/임베딩/벡터 일치율/LLM 토론.
- **DART 융합은 범위 밖**(별도 수립). 메타러너 인터페이스(`ml_inferences` `model_name=src_dart`)만 추후
  합류 가능하게 열어둔다.

## 9. 수용한 잔여 리스크 (모니터링, 차단 아님)

- 크롤러 안정성(hiring 포털 변경)·Report 추출 품질(needs_review)은 데이터 품질 모니터로 관리.
- 메타러너 lift가 베이스라인 대비 우위를 못 내면 → 해당 소스 가중 0 수렴으로 자연 배제, L6 채택 게이트가 차단.
  (베이스라인 없는 정책상 전 소스 lift 미달 시 발행 보류.)
- 2-DB 컷오버 시 `api.*` view → publisher 전환 구간의 백엔드 호환성은 그린필드 부트스트랩으로 일괄 전환.
