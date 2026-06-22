# Worker 파이프라인 — 보류 항목(Deferred / Known Items)

게이트형 워커 스택(PR1~7, #341–#357) 코드 리뷰에서 **의도적으로 보류**한 항목 추적표.
모두 머지를 막지 않는 비-블로킹 사안이며, "지금 고치면 과설계/리스크"이거나 운영 단계에서
다루는 게 적절한 것들이다. 각 항목은 **수정 트리거**(언제 손대야 하는지)와 **수정 방법**을 적어,
조건이 충족되면 바로 작업할 수 있게 한다.

> 참고: 아키텍처 차원의 후속 과제(게이트1/quarantine 일반화, 메타러너 학습 파이프라인,
> GPU 모델 검증, published→veto 윈도우)는 [`worker-redesign.md`](./worker-redesign.md) "후속 과제"
> 섹션에서 관리한다. 이 문서는 코드 리뷰에서 나온 **구현 세부 보류 항목**을 다룬다.

---

## 1. `ml_inferences.gate_passed` 컬럼이 사실상 항상 `TRUE`
- **위치**: `services/agent-worker/app/ml/inference.py` (`upsert_inference(..., gate_passed=True)`),
  `database/migrations/018_ml_inferences.sql`
- **현상**: 추론 전에 `model_registry.resolve_models()`가 게이트 통과+가용 모델만 남기므로,
  적재 단계에 도달하는 행은 전부 `gate_passed=True`. 즉 컬럼에 `FALSE`가 들어올 경로가 없다.
- **보류 사유**: 버그 아님. 컬럼은 "게이트 탈락 모델도 관측용으로 기록"하는 미래 용도를 위한 여지.
- **수정 트리거**: (a) 게이트 탈락 모델의 예측도 비교/디버깅 목적으로 남기고 싶을 때, 또는
  (b) 컬럼이 영구히 무의미하다고 판단될 때.
- **수정 방법**: (a)면 `resolve_models()` 대신 전체 후보를 추론하고 `gate_passed`를 모델별 실제
  게이트 결과로 기록(메타러너는 이미 `gate_passed` 필터로 읽으므로 결합엔 영향 없음).
  (b)면 컬럼 드롭 마이그레이션(forward-only).

## 2. `ml_inferences` / `meta_signals` 무한 증가(retention 부재)
- **위치**: `database/migrations/018_ml_inferences.sql`, `019_meta_signals.sql`
- **현상**: 종목×asof×모델×horizon 단위로 행이 계속 쌓이고 보존/정리 정책이 없다. FK는 `stocks`만.
- **보류 사유**: 현 데이터량에선 무방. 조기 파티셔닝/정리는 과설계.
- **수정 트리거**: 테이블 행수/디스크가 운영상 문제가 되거나, 일/월 단위 적재가 정착돼 증가율이
  예측되기 시작할 때.
- **수정 방법**: `asof_date` 기준 월 파티셔닝 또는 N일 경과분 정리 잡(예: 관측/리포트 보존 윈도우와
  정렬). 멱등 upsert라 재적재는 안전하므로 오래된 파티션 드롭이 단순.

## 3. synthesis가 최근 `meta_signal`을 best-effort로 참조(asof/run 미정렬)
- **위치**: `services/agent-worker/app/synthesis/tasks.py` (`MetaSignalRepository.latest_for_stock`)
- **현상**: 끝단 리포트가 `(stock_id, run_key)`의 가장 최근 `meta_signal`을 asof/run 정렬 없이
  참조한다. `META_COMBINE`가 같은 run에서 늦으면 직전 run 값을 쓸 수 있다(결합 변동성은
  리포트의 보조 참조 필드라 판정엔 영향 없음).
- **보류 사유**: 의도된 best-effort. `published→veto 윈도우`와 함께 [`worker-redesign.md`] 후속
  과제에 명시됨. 엄격 정렬은 동기화 비용 대비 이득이 작음.
- **수정 트리거**: 리포트의 `ml_risk`가 종목/asof 정합이 반드시 맞아야 하는 소비처(예: 정밀 백테스트
  대조)가 생길 때.
- **수정 방법**: `latest_for_stock` 대신 `get(stock_id, run_key, asof_date, horizon)`로 동일 run/asof를
  명시 조회하고, 부재 시에만 best-effort 폴백.

## 4. GPU 모델 FALLBACK proxy의 직접 호출 footgun
- **위치**: `packages/vol-models/vol_models/models/gpu_kronos.py`,
  `gpu_chronos2.py` (`predict_fallback`, `__main__`의 proxy 경로)
- **현상**: torch/모델 라이브러리 미설치 시 `predict_fallback`이 RV 프록시(진짜 모델 출력 아님)를
  반환한다. 파이프라인은 `model_registry.is_available()`(HAVE_LIB 플래그) 게이트로 걸러 도달하지
  않지만, harness/스크립트에서 `predict_fallback`을 **직접** 호출하면 가짜값이 섞일 수 있다(경고 print만 있음).
- **보류 사유**: vol-benchmark harness가 의도적으로 이 proxy에 의존(라이브러리 없는 CI에서 파이프 점검).
  파이프라인 경로는 게이트로 안전.
- **수정 트리거**: GPU 모델을 실제 GPU 호스트에서 검증·화이트리스트 편입할 때
  ([`worker-redesign.md`] "GPU 모델 검증"과 함께).
- **수정 방법**: proxy 결과에 `is_proxy=True` 같은 표식을 부여하거나, 프로덕션 경로에서 proxy를
  명시적 env 플래그(`VOL_BENCH_ALLOW_FALLBACK`) 없이는 예외로 만들기.

## 5. 추론 매직넘버(기본값)
- **위치**: `services/agent-worker/app/ml/inference.py` (`DEFAULT_HORIZON=10`,
  `DEFAULT_LOOKBACK_SESSIONS=400`, `DEFAULT_SEED=42`), `model_registry.py` ModelSpec `cfg`
  (`context_len`, `n_samples`)
- **현상**: 추론 파라미터가 코드 기본값. horizon/lookback/seed는 env(`ML_*`)로 오버라이드 가능,
  GPU `context_len`/`n_samples`는 `ModelSpec.cfg` 고정.
- **보류 사유**: 합리적 기본값이고 핵심 축은 이미 env 오버라이드 가능. 모델 cfg는 모델 특성이라
  코드 상수가 적절.
- **수정 트리거**: 운영 중 horizon/lookback을 환경별로 자주 바꾸거나, GPU cfg를 런타임 튜닝해야 할 때.
- **수정 방법**: 필요한 cfg 키를 env→`ModelSpec.cfg` 주입 경로로 노출(레지스트리 빌드 시점에 매핑).

---

### 이미 처리된 리뷰 항목(참고)
아래는 같은 리뷰에서 나와 **이미 수정**된 항목(브랜치 `fix/worker-review-followups` / PR #359):
veto 부분문자열 오탐, bull/bear 매핑, 투자조언 필터 오탐, stacking 집계 일치, `_task_context`/
`_int_list` DRY 통합(4곳), LLM base_url 중복 제거, GPU repo id의 cfg화, langsmith lru_cache 시크릿 키 제거.
