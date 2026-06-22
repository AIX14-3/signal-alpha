# Worker 게이트형 파이프라인 재설계

signal-alpha agent-worker를 `vol-benchmark`의 [`architecture.mermaid`](./architecture.mermaid)
형태(게이트형 결정론 파이프라인 + 백테스트 검증 ML + 끝단 LLM 설명)로 전환한 결과를 정리한다.

핵심 원칙: **판정(점수·방향·발행)은 결정론 규칙·ML·게이트가 정하고, LLM은 설명만 한다.**
모든 ML/게이트 단계는 산출물·키가 없을 때 기존 거동으로 **폴백**해 동작을 보존한다.

## 단계 ↔ 큐 task_type 매핑

| 다이어그램 단계 | 분류 | 구현 | task_type |
|---|---|---|---|
| 수집기 | det | `app/collectors/*` | `collect_dart`, `collect_report`, … |
| 게이트1 (데이터 검증) | det | hiring 3c 검증 게이트, DLQ(`dead_letter`), quarantine(`hiring_quarantine`) | 수집/정규화 단계에 분산 |
| 소스별 전처리 (분석기) | det | `app/analyzers/*` — 규칙·통계지표·임베딩으로 결정론 피처 산출 (DART 규칙+공시 임베딩, PRICE 지표, DataLab 지표, Report RAG 검색). **생성형 LLM은 쓰지 않음** | `normalize_*`, `analyze_*` |
| 적재 (PG·pgvector) | det | `orchestrator/persistence.py`, report 임베딩 | — |
| 게이트2 (결합·신호 품질) | ml | `AggregateSignalTaskHandler` (consensus·warning_level·needs_review·is_published) | `aggregate_signal` |
| ML/DL 추론 (게이트 통과 모델만) | ml | `app/ml/inference.py` + `model_registry`(2중 게이트) + `packages/vol-models` | `ml_infer` |
| 메타러너 결합 (stacking) | ml | `app/ml/meta_learner.py` + `meta_combine.py` → `meta_signals` | `meta_combine` |
| 리스크 veto (치명 키워드) | det | `app/gates/risk_veto.py` + `rules/veto_keywords.py` | `risk_veto` |
| LLM 종합·설명 | llm | `app/synthesis/` + `schemas/risk_report.py` | `synthesize` |
| 결과물 리스크 리포트(JSON) | out | `final_signals` + `RiskReport` | — |
| LangSmith 관측 | obs | `app/observability/langsmith.py` (관측만) | — |

## 큐 체인 (자동 enqueue)

**목표 순서(architecture.mermaid, 기획):** 판정이 단계를 빠짐없이 **선형**으로 통과하고,
끝단 LLM 뒤에서 veto가 **정제 루프**로 동작한다(치명 키워드라고 버리지 않는다).

```
COLLECT_<SRC> → 게이트1 → NORMALIZE_<SRC> → (EMBED_DART) → ANALYZE_<SRC>   (소스별 전처리)
        └─(fan-in)→ ML_INFER → META_COMBINE → 게이트2(신호·모델 품질)
                       ├─ 약함 → needs_review (미발행)
                       └─ 발행 → SYNTHESIZE(LLM 종합) → RISK_VETO(데이터+LLM텍스트 치명키워드)
                                    ├─ 키워드 없음 → 발행(final_signals)
                                    ├─ 키워드 & 미정제 → SYNTHESIZE 정제(1회, 리스크 강조) ↺
                                    └─ 키워드 & 정제후에도 치명 → needs_review (미발행)
```

게이트2는 메타러너 결합(meta_signal: 모델 신뢰도) + consensus·warning_level을 보고 발행/needs_review를
판정한다. veto는 **LLM 종합 아래**에서 동작하고, 치명 키워드가 나오면 LLM 정제를 1회 거쳐 발행한다.

### ⚠️ 현재 코드(미정렬) — 재정렬 필요
아직 아래 갈래형이다 — `AGGREGATE_SIGNAL`(게이트2)이 `ML_INFER` **앞**에서 ML 가지·veto 가지를
동시 fan-out 하고, veto가 LLM **앞**에서 치명 키워드 시 미발행(`apply_risk_veto`)한다.

```
        └─(fan-in)→ AGGREGATE_SIGNAL
                       ├─→ ML_INFER → META_COMBINE               (meta_signals 적재)
                       └─(발행 신호)→ RISK_VETO → SYNTHESIZE      (리스크 리포트)
```

### 재정렬 구현 계획 (TODO — 오케스트레이션 변경)
1. `dart/tasks.py`: 분석 후 트리거를 `AGGREGATE_SIGNAL` → **`ML_INFER`** 로. 단, AGGREGATE가
   나중에 필요로 하는 컨텍스트(stock_code·signal_date·aggregation_key)를 **불투명 `aggregate_ctx`**
   로 실어 ML→META가 그대로 통과시키게 한다.
2. `ml/inference.py`·`ml/meta_combine.py`: **skip이어도 다음 단계를 항상 enqueue** 한다
   (⚠️ **함정**: OHLCV 없는 종목은 ML이 graceful skip → 지금처럼 "ML과 무관히 AGGREGATE 실행"을
   유지하려면 ML_INFER는 skip이어도 META를, META는 항상 AGGREGATE를 enqueue해야 리포트가 끊기지 않음).
   META_COMBINE이 `aggregate_ctx`로 **AGGREGATE_SIGNAL**을 enqueue.
3. `aggregation/tasks.py`: ML_INFER/RISK_VETO enqueue 제거. `meta_signal`(모델 신뢰도)을 읽어
   발행 판정에 반영. 발행 시 **SYNTHESIZE** enqueue.
4. `synthesis/tasks.py`: 종합 후 **RISK_VETO** enqueue(컨텍스트에 `refined` 플래그 전달).
   `refined=true`면 리스크 강조 정제 프롬프트.
5. `gates/risk_veto.py`: 검사 입력에 **LLM 종합 텍스트**(final_signal.summary 등) 추가.
   치명 키워드 & `refined!=true` → **SYNTHESIZE(refine=true) 재enqueue**(미발행 금지);
   `refined=true`인데도 치명 → `apply_risk_veto`(needs_review). 키워드 없으면 종료(발행 유지).
6. 각 핸들러 체인 테스트 갱신(현 갈래형 가정 → 선형+정제 루프).

- `ML_INFER`는 generic `POST /internal/tasks/ml_infer/enqueue`·`/run` 으로 수동/스케줄 트리거도 가능.
- 재정렬되면 META_COMBINE → 게이트2 → SYNTHESIZE 가 한 체인이므로 현재의 best-effort meta 참조는 제거된다.

## 2중 모델 게이트 (`model_registry`)

1. **백테스트 게이트** (`ML_GATE_PASSED_MODELS`, 기본 `ewma,har_rv,garch,lightgbm`) — vol-benchmark
   `comparison.csv`(DM<0 & p<0.05) 채택분만 화이트리스트. EWMA는 기준선으로 항상 포함.
2. **가용성 게이트** (`HAVE_*` 플래그) — 백엔드(arch/lightgbm/torch) import 가능할 때만.
   GPU 모델(Kronos/Chronos-2)은 CPU 호스트에서 자동 제외.

## 신규 테이블 (forward-only 마이그레이션)

- `018_ml_inferences` — 모델별 `pred_vol`(실패 시 NULL+error), `gate_passed`, `device`. 자연키 멱등.
- `019_meta_signals` — 결합 변동성/신뢰도/method(`stacking`|`equal_fallback`|`empty`)/weight_breakdown.

리스크 veto·끝단 종합은 신규 테이블 없이 기존 `final_signals`(is_published/needs_review/warning_level/summary)를 재사용한다.

## 환경 변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `ML_GATE_PASSED_MODELS` | `ewma,har_rv,garch,lightgbm` | 백테스트 채택 모델 화이트리스트 |
| `ML_HORIZON` / `ML_LOOKBACK_SESSIONS` / `ML_SEED` | 10 / 400 / 42 | 추론 파라미터 |
| `ML_META_LEARNER_ARTIFACT` | `app/ml/artifacts/meta_learner.json` | stacking 학습 가중(없으면 균등 폴백) |
| `RISK_VETO_KEYWORDS` | (기본 목록에 가산) | 치명 키워드 추가 |
| `SYNTHESIS_USE_LLM` / `SYNTHESIS_LLM_PROVIDER` / `SYNTHESIS_LLM_MODEL` | off / gemini / — | 끝단 LLM 종합(미설정 시 결정론 폴백) |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_ENDPOINT` | off / — / signal-alpha / — | LLM 호출 관측(관측만) |

## 결정론/ML/LLM 경계

- **결정론**: 수집·게이트1·소스별 전처리(규칙·통계지표·임베딩)·적재·리스크 veto. 같은 입력 → 같은 출력.
  공시 텍스트는 임베딩(pgvector)+규칙추출로, DataLab 검색 시계열은 통계지표로 피처화한다.
- **ML/DL**: vol-benchmark 모델 추론·메타러너 stacking·결합 품질(게이트2). 백테스트로 검증.
  소스별 전처리 피처는 판정 입력이므로 **결정론(임베딩은 고정 가중치)** 이어야 백테스트·메타러너 학습이 가능하다.
- **LLM**: **끝단 종합(설명)만**. 소스 전처리 단계에는 생성형 LLM을 두지 않는다(비결정 입력이 판정을 흔드는 것을 차단).
  **수치/판정 불변, 설명만**, 투자조언 차단.
- **관측**: LangSmith는 LLM 호출 trace만(점선). 흐름·결과·지연에 영향 없음(스레드 오프로딩·실패 삼킴).

## 후속 과제

- **게이트1/quarantine 일반화**: 현재 검증 게이트·레코드 격리는 hiring에 구현됨(DLQ는 공용).
  두 번째 소비 소스가 생기면 공용 헬퍼로 추출(현재는 투기적 추상화를 피해 보류).
- **메타러너 학습 파이프라인**: `harness/`에서 vol-benchmark OOF 예측으로 `meta_learner.json` 산출.
- **GPU 모델 검증**: Kronos/Chronos-2는 GPU 호스트에서 실행 검증 후 화이트리스트 편입.
- **published→veto 윈도우**: 발행 후 veto가 비동기로 보류 — 큐 특성상 짧은 노출 가능(인지).
