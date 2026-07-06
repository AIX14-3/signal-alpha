# Signal α — 데이터 파이프라인

소스 데이터가 어떻게 수집되어 사용자에게 보여줄 최종 시그널이 되는지를 설명합니다.
깊은 레이어 정의는 `spec/data-foundations-and-l1-l10-workflow.md`,
`spec/data-layers-l2-l10-spec.md`, 스키마 기준은 `database/migrations/`(+`spec/db-schema-spec.md`)입니다.

## 처리 모델: collect → normalize → analyze → aggregate

각 단계는 `processing_queue` 기반 **독립 작업**으로 분리됩니다. Collector는 원본을 수집/저장하고 후속
작업을 큐에 등록하며, Analyzer는 DB의 정규화/승인 데이터만 읽습니다(외부 API 직접 호출 금지).

**(#11 업데이트)** 스케줄러 인스턴스(`run_scheduler_instance.py`)가 수집·종목 분석 작업을 주기 인큐하고,
**워커의 큐 드레인 데몬**(`app/orchestrator/queue/drain_daemon.py`, `QUEUE_DRAIN_DAEMON_ENABLED`)이
`processing_queue`를 체인 순서대로 끝단(`PUBLISH_SIGNALS` 발행)까지 연속 소비합니다(advisory-lock 단일 기동,
단발/CI 검증은 `run_worker_drain.py`). 컴퓨트 토폴로지(워커·수집기·스케줄러 분리)는
[architecture-diagram.md](./architecture-diagram.md) 참조.

```text
collect   원본 수집 → raw_documents (+ 소스별 raw_details)         → 다음 단계 enqueue
normalize 표준 문서/이벤트/지표 생성 → source_documents, signal_events, signal_metrics
analyze   소스별 규칙/LLM 분석 → analysis_results, agent_results
aggregate 소스 결과 통합 → final_signals (소스 방향성 일치도·근거 정리)
```

## 소스별 경로

| 소스 | 수집 | 주요 처리 |
|---|---|---|
| **DART** | OpenDART corp_code/list/document.xml + ownership API | `raw_documents` + `dart_raw_details` / `dart_ownership_events` → 정규화 → features-only 분석. 선택적 LLM은 근거 추출용이며 판정·점수는 내지 않음 |
| **Report** | 네이버 리포트 목록 + PDF | `report_raw_details`(PDF 파싱: 목표가·의견·근거) → `report_valuation_facts`(EPS·적용/내재 배수) → 결정론 분석. 원문 PDF 미노출, 구조화 fact·링크 중심 |
| **PRICE** | 키움 REST (수집기 인스턴스 `run_collector_instance.py`, 워커 내장 on/off는 `PRICE_COLLECTOR_ENABLED`) | `price_snapshots`, `ohlcv_data` 저장 → PRICE analyzer는 **DB만** 읽어 분석 |
| **Alternative** | 채용 / 특허(KIPRIS) / 네이버 DataLab / SEC | 소스별 collector→analyzer. DataLab은 카테고리 기반 키워드 검색량 |

소스별 collector/analyzer는 `agent-worker/app/collectors/{dart,report,price,datalab,hiring,patent,sec}` 및
`analyzers/{dart,report,price,datalab,hiring,patent}` 아래에 있습니다.

## 핵심 DB 테이블 흐름 (MVP)

```text
stocks
  └─ raw_documents
       ├─ dart_raw_details / report_raw_details
       │     └─ report_valuation_facts (리포트 목표가·EPS·배수)
       └─ source_documents
            └─ signal_events
                 └─ signal_metrics
                      └─ analysis_results
                           └─ agent_results
                                └─ final_signals   → 사용자에게 노출
```

- 운영/상태: `processing_queue`, `collector_runs`, `dart_collection_states`, `validation_logs`
- 가격: `price_snapshots`, `ohlcv_data`
- DataLab 수집 경로: `datalab_raw_documents → datalab_raw_details → processing_queue(stock_id=NULL)`

> 위는 빠른 이해용 요약입니다. 컬럼·제약·인덱스의 **유일한 기준은 `database/migrations/`**.

## 현재 구현 상태 (요약)

> 정확한 최신 상태는 `AGENTS.md`와 코드를 확인하세요. 기획 문서를 구현 완료로 취급하지 않습니다.

- **구현됨**: DART 큐 핸들러 `collect_dart` / `normalize_dart` / `analyze_dart` 및 ownership 경로 `collect_dart_ownership` / `normalize_dart_ownership`.
- **구현됨**: 가격 수집은 **수집기 인스턴스**(`run_collector_instance.py`)로 동작하며 `price_snapshots`/`ohlcv_data`
  적재(`PRICE_COLLECTOR_ENABLED`로 워커 내장 on/off 가능, 단일 통합 기동 시 워커 lifespan에 내장)(#11 업데이트).
  PRICE analyzer는 DB만 읽음(키움 API 직접 호출 금지).
- **구현됨**: DataLab 카테고리 기반 수집 경로.
- **구현됨**: Report 큐 경로 `collect_report → process_report → normalize_report → analyze_report → aggregate_signal` (결정론 밸류에이션 fact 추출). `analyze_report` 가 `aggregate_ctx.source_analysis_result_ids`에 Report `analysis_result_id`를 담아 **AGGREGATE 로 직접** 넘기며(과거 경유하던 vol ML 채널 `ml_infer` 는 C안 Phase 1 #585 에서 제거), Aggregator는 `REPORT`를 최종 `score_breakdown.REPORT` 근거 소스로 수용합니다. Report는 valuation payload를 보존하지만 현재 점수 산정 소스에는 포함하지 않습니다.
- **폐지됨**: 리포트 PDF **임베딩/RAG 검색 런타임**은 제거되었습니다(`embed_report`/RAG retriever/Report Agent 부재). `report_chunks` 스키마가 남아 있을 수 있지만 현재 Report 런타임에서는 `report_chunks`를 적재하거나 조회하지 않습니다. 리포트 분석은 RAG가 아니라 `report_valuation_facts` 기반 결정론 추출입니다. 자세한 현황은 `spec/report-rag-current-state.md`.
- **구현됨(#11 업데이트)**: 워커 드레인 데몬이 큐를 끝단까지 소비한다 — 주가(PRICE)는 `analyzers/price`
  의 **기술지표 규칙**으로 `RiskReport.price_prediction`을 **별도 제공**합니다. DART는 현재
  `direction="unknown"`, `data_status="no_signal"`인 근거/커버리지 소스로 집계에 합류하므로
  `score_breakdown.DART`와 끝단 LLM 종합(`SYNTHESIZE`)에는 남지만 숫자 `final_score` 평균에는 들어가지 않습니다.
  `backfill_dart_labels` 기반 이벤트스터디 라벨 백필 및 DART 소스 ML 채널은 운영 경로에서 제거되었습니다.
  Report도 valuation payload와 근거를 보존하지만 현재 점수 산정 소스에는 포함하지 않습니다. 라우팅 상세는
  [architecture-diagram.md](./architecture-diagram.md). legacy `report_raw`, `report_signal`은 제거되었으며 신규 경로에서 사용하지 않습니다.

## LLM·분석 규칙

- 수치 값은 원천 데이터/DB row에서 가져오며 LLM이 생성하지 않습니다.
- LLM 출력은 저장·노출 전에 JSON Schema/Pydantic으로 검증합니다.
- LLM timeout·잘못된 JSON·금지 표현 감지 시 결정적 규칙 기반 fallback을 제공합니다.

관련 스펙: `spec/dart-collector-analyzer-spec.md`, `spec/analyzer-raw-access-conformance.md`,
`spec/report-rag-current-state.md`, `spec/kiwoom-rest-spec.md`, `spec/cross-layer-orchestration-and-risks.md`,
`spec/final-signal-aggregator-spec.md`. DataLab 키워드는 [datalab-keyword-validation.md](./datalab-keyword-validation.md),
[datalab-keyword-lifecycle.md](./datalab-keyword-lifecycle.md) 참고.
