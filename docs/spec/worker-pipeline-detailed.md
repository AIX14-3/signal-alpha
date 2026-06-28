# 워커 드레인 파이프라인 — 상세 설계도 (코드 수준)

> `services/agent-worker` 큐 드레인 파이프라인을 **코드 수준으로 상세히** 그린 설계도다.
> 개요(토폴로지·DB 경계)는 [architecture-diagram.md](../architecture-diagram.md), 팀 핸드오프와 plug-in
> 절차는 [spec/worker-design-and-handoff.md](./worker-design-and-handoff.md) 참고. 동작 기준은 항상 코드/테스트.
> 출처: `app/orchestrator/queue/drain_daemon.py`(`DRAIN_ORDER`), 각 핸들러의 `enqueue(...)`.
> 최종 갱신: 2026-06-28.

이 문서는 두 장의 다이어그램을 담는다.
- **A. 현 상태** — origin/main 기준 실제 드레인 파이프라인. 메타러너 **return(SRC) 채널은 코드만 있고
  라이브 생산자가 없어 OFF**(점선).
- **B. ① 이후** — §5 핸드오프의 OFF plug-in 스위치 2개를 **env gate 기본-OFF로 배선**했을 때의 델타.

---

## A. 현 상태 — 큐 드레인 파이프라인

`DRAIN_ORDER`(드레인 효율용 정렬, 정확성은 `drain_until_idle` 의 progressed 루프가 보장):
수집 → 정규화 → 분석(소스별) → ML/DL(2 채널) → 집계 → 게이트 → 종합 → 발행.

```mermaid
flowchart TB
  sch["스케줄러 유닛<br/>/internal/schedules/* 주기 인큐"] --> Q[("processing_queue<br/>(수집 DB)")]

  subgraph SRC_DART["DART"]
    cd[COLLECT_DART] --> nd[NORMALIZE_DART] --> ad[ANALYZE_DART]
  end
  subgraph SRC_REPORT["증권사 리포트"]
    cr[COLLECT_REPORT] --> prc[PROCESS_REPORT] --> nr[NORMALIZE_REPORT] --> ar[ANALYZE_REPORT]
  end
  subgraph SRC_ALT["Alternative — coalesce(3:1 방지)"]
    nh[NORMALIZE_HIRING] --> eh[ENRICH_HIRING]
    npa[NORMALIZE_PATENT] --> ep[ENRICH_PATENT]
    ndl[NORMALIZE_DATALAB]
    eh --> aa[ANALYZE_ALTERNATIVE]
    ep --> aa
    ndl --> aa
  end
  ap["ANALYZE_PRICE<br/>주가 ML/DL → score_breakdown.PRICE"]

  Q --> cd
  Q --> cr
  Q --> nh
  Q --> npa
  Q --> ndl
  Q --> ap

  %% ── vol 채널 (run_key=ML) : OHLCV 변동성 ──
  ad -- "enqueue ML_INFER" --> mi["ML_INFER<br/>vol 모델(ewma/har_rv/garch/tcn)<br/>게이트 통과 모델만"]
  ar -- "enqueue ML_INFER" --> mi
  mi --> mc["META_COMBINE<br/>stacking(학습 가중)·균등 폴백<br/>run_key=ML → meta_signals.combined_vol"]

  %% ── 소스 return 채널 (run_key=SRC) : 현재 생산자 없음(OFF) ──
  si["SRC_INFER (등록됨·DRAIN_ORDER 포함)<br/>소스 정형피처 → src_* base 모델(LightGBM)<br/>아티팩트: app/ml/artifacts/source_models/*.txt"]:::off
  si -. "성공 예측 시 enqueue" .-> rc["RETURN_COMBINE<br/>run_key=SRC → meta_signals(final_score/direction/confidence)<br/>+ final_signals return 컬럼 오버레이"]:::off
  noprod["⚠ 라이브 경로에서 SRC_INFER 를<br/>아무도 인큐하지 않음 = OFF 지점"]:::off -.-> si

  %% ── 집계(fan-in) ──
  ad --> agg
  ar --> agg
  aa --> agg
  ap --> agg
  mc --> agg
  agg["AGGREGATE_SIGNAL<br/>final_score = SCORING_SOURCES{DART,ALTERNATIVE} 평균(점수 안 뒤집음)<br/>PRICE/REPORT = 근거(점수 산입 X)<br/>fan-in: analysis_results + meta(ML)"]

  %% ── 게이트(발행 판정) → 종합 → veto → 발행 ──
  %% AGGREGATE 가 게이트2(신호·모델 품질, is_published) 역할: 발행분만 종합/발행으로 인큐.
  agg -- "발행분(is_published) + 근거" --> sy["SYNTHESIZE — 끝단 LLM(temp=0, 점수 불변)<br/>입력: 집계점수 + price_prediction + report_valuation<br/>+ evidence + ml_risk(meta_signals run_key=ML)<br/>gate SYNTHESIS_USE_LLM · 미설정 시 결정론 폴백<br/>prompts/synthesis_v1.md"]
  agg -- "발행분 → 백엔드 복사" --> pub["PUBLISH_SIGNALS<br/>→ 백엔드 DB(앱레벨 발행)"]
  sy -- "종합 뒤 enqueue" --> rv["RISK_VETO<br/>치명 키워드 → 미발행/needs_review"]
  rv -. "정제 필요 시 1회 재종합" .-> sy
  pub --> api["api.signals_current / signal_detail"] --> web["web 대시보드"]

  %% ── 추천 라인 ──
  fs[("final_signals · meta_signals<br/>(수집 DB)")]
  agg -.-> fs
  rc -.-> fs
  fs --> rec["run_recommend.py<br/>recommendation_score = dir_w·conf_w·vol_w<br/>변동성 역가중 → recommendations(rank)"]

  %% ── 안정화 ──
  rv -. "미발행" .-> dlq[("dead_letter")]
  Q -. "실패 retry++ / 한도초과" .-> dlq

  classDef off stroke-dasharray:5 4,fill:#f3f4f6,stroke:#9ca3af,color:#6b7280;
```

**핵심 사실(코드 대조용)**
- **vol 채널(run_key=ML)**: `ANALYZE_DART`/`ANALYZE_REPORT` 가 `ML_INFER` 인큐
  (`dart/tasks.py`, `report/tasks.py`). `ML_INFER`(`ml/inference.py`)는 OHLCV 를 읽어 변동성 예측 →
  `META_COMBINE`(`ml/meta_combine.py`)가 stacking 결합 → `meta_signals.combined_vol`(run_key=ML).
- **소스 return 채널(run_key=SRC)**: `SRC_INFER`(`ml/source_inference.py`)·`RETURN_COMBINE`
  (`ml/return_combine.py`) 핸들러는 등록·DRAIN_ORDER 포함이지만 **라이브 경로에서 `SRC_INFER` 를
  인큐하는 코드가 없다**(= OFF). 켜지면 `meta_signals`(run_key=SRC)의 return 컬럼과 `final_signals`
  return 오버레이를 채운다. vol 채널과 자연키로 분리되어 `combined_vol` 을 오염시키지 않는다(D4).
- **집계**: `aggregation/tasks.py` — `final_score` 는 `SCORING_SOURCES={DART, ALTERNATIVE}` 평균만으로
  산출(점수를 뒤집지 않음). PRICE/REPORT 는 근거로만 수집. ALT 다수 소스는 coalesce 로 1 peer 화.
- **종합**: `synthesis/tasks.py` — LLM 은 **점수 불변**, "이유만" 서술. `price_prediction` 은
  `score_breakdown.PRICE` 에서 분리한 **별도 정량 신호**. `ml_risk` 는 `meta_signals`(run_key=**ML**)만 읽음.
- **게이트/발행 순서**: `AGGREGATE_SIGNAL` 이 게이트2(신호·모델 품질, `is_published`) 역할을 하며,
  **발행분만** `SYNTHESIZE`(run_key=ML)와 `PUBLISH_SIGNALS`(백엔드 DB 설정 시)를 각각 인큐한다
  (`aggregation/tasks.py`). `RISK_VETO` 는 종합 **뒤**에 동작한다(`SYNTHESIZE` 가 `RISK_VETO` 인큐):
  치명 키워드 시 LLM 정제 루프(RISK_VETO→SYNTHESIZE 재인큐 1회), 이후에도 치명이면 미발행/needs_review.
  `PUBLISH_SIGNALS` 가 백엔드 DB 로 앱레벨 발행 → `api.signals_current` → web.

---

## B. ① 이후 — 메타러너 return(SRC) 채널 연결 (env gate 기본-OFF)

①은 **새 로직을 추가하지 않는다.** 이미 머지된 SRC 채널(#525/#546)을 §5 핸드오프의 OFF 스위치
2개로 **연결**할 뿐이며, 두 스위치 모두 **기본 OFF** 라 끄면 A 와 동작이 byte-for-byte 동일하다.

```mermaid
flowchart TB
  aa["ANALYZE_ALTERNATIVE"] -- "SRC_INFER_ENABLED<br/>(기본 off)" --> si
  ar["ANALYZE_REPORT"] -- "SRC_INFER_ENABLED<br/>(기본 off)" --> si["SRC_INFER<br/>(스위치1: 라이브 인큐 연결)"]
  si --> rc["RETURN_COMBINE"]
  rc --> ms[("meta_signals<br/>run_key=SRC")]
  rc --> fso[("final_signals<br/>return 오버레이")]
  ms -- "SYNTHESIS_INCLUDE_META_SRC<br/>(기본 off)" --> sy["SYNTHESIZE (스위치3)<br/>source_prediction 을 price_prediction 옆<br/>별도 예측치로 LLM 컨텍스트에 노출 (점수 불변)<br/>gate ON 시 prompts/synthesis_v2.md"]
  fso -.-> apiv["api.signals_current<br/>(소비자/web 자동 전파)"]
  sy --> rep["RiskReport(JSON).source_prediction"]

  classDef on stroke:#16a34a,stroke-width:2px;
  class si,rc,sy on;
```

**두 OFF 스위치 (켜는 지점)**

| 스위치 | env gate (기본 off) | 연결부 | OFF일 때 |
|---|---|---|---|
| 1. SRC_INFER 라이브 인큐 | `SRC_INFER_ENABLED` | `ANALYZE_ALTERNATIVE`/`ANALYZE_REPORT` 핸들러 끝 | 인큐 0건, SRC 채널 dormant |
| 3. SYNTHESIZE→LLM 노출 | `SYNTHESIS_INCLUDE_META_SRC` | `SYNTHESIZE` 가 `meta_signals(run_key=SRC)` read → `source_prediction` | LLM 컨텍스트·RiskReport JSON·프롬프트 모두 A 와 동일 |

(스위치 2 = base 모델 아티팩트 학습은 `train_source_models.py`. 아티팩트가 없으면 `SRC_INFER` 예측=None →
`RETURN_COMBINE` 미인큐로 **안전 no-op**.)

**OFF-safe 보장**
- 두 gate 는 `_env_bool(..., default=False)` 로 정의(미설정=OFF).
- 스위치3 은 `source_prediction` 이 있을 때만 `_llm_context`/`RiskReport.to_dict`/결정론 내러티브에 키를
  추가하고, 프롬프트도 gate ON 시에만 v2 를 선택 → OFF 출력은 현행과 동일.
- **주의(독립성)**: 스위치1만 켜도 `RETURN_COMBINE` 이 `final_signals` return 컬럼을 오버레이하므로
  소비자 노출이 바뀐다(스위치3 와 독립). 집계 점수 산식(SCORING_SOURCES)은 어느 경우든 불변.

> 권장 순서(핸드오프 §5): 데이터/라벨이 쌓이기 전엔 OFF 가 정상. 결정론/LLM 라인으로 먼저 운영하고,
> 데이터가 쌓이면 컨센서스 상관이 높은 REPORT 부터 학습 채널을 켠다.
