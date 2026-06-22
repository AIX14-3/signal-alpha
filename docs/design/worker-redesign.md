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

```
COLLECT_<SRC> → 게이트1 → NORMALIZE_<SRC> → ANALYZE_<SRC>        (소스별)
        └─(fan-in)→ AGGREGATE_SIGNAL
                       ├─→ ML_INFER → META_COMBINE               (meta_signals 적재)
                       └─(발행 신호)→ RISK_VETO → SYNTHESIZE      (리스크 리포트)
```

- `ML_INFER`는 generic `POST /internal/tasks/ml_infer/enqueue`·`/run` 으로 수동/스케줄 트리거도 가능.
- `SYNTHESIZE`는 `META_COMBINE`가 같은 run에 늦으면 **다음 run의 최근 meta_signal**을 참조(best-effort).

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
