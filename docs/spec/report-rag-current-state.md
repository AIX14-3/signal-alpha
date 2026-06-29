# 증권사 리포트 수집/밸류에이션 현재 구현 상태

> ⚠️ **#11 업데이트**: 주가(PRICE)는 기술지표 규칙으로 `RiskReport.price_prediction`을 **별도 제공**(ML/DL 주가는 `src_price` 별개; 집계 `final_score`는 `{DART, HIRING, PATENT, DATALAB}` 소스별 독립 유지, 뒤집지 않음). DART/REPORT=근거(LLM/결정론, 헤드라인 점수엔 메타러너 미사용). 발행은 `RISK_VETO` 게이트 뒤. 워커는 큐 드레인 데몬으로 발행까지 연속 소비. 상세 [architecture-diagram.md](../architecture-diagram.md).

최종 갱신일: 2026-06-25

## 목적

이 문서는 증권사 리포트 수집과 분석 흐름의 현재 구현 상태를 정리한 인수인계용 문서입니다. 새 담당자는 이 문서를 기준으로 현재 코드가 어디까지 연결되어 있고, 어떤 부분이 아직 비어 있는지 확인할 수 있습니다.

Signal Alpha는 투자 추천 서비스가 아닙니다. 증권사 리포트 데이터는 데이터 방향성, 근거, 소스 간 일치도, 데이터 정합성, 추가 확인 필요 여부를 보여주는 사용자 판단 보조 정보로만 다뤄야 합니다. 사용자-facing 문구에서 매수, 매도, 보유 추천이나 투자 타이밍 알림처럼 보이는 표현을 사용하지 않습니다.

## 기준 자료

오래된 기획 문서보다 현재 코드, 테스트, migration을 우선합니다.

- 스키마: `database/migrations/001_baseline.sql`, `database/migrations/010_report_s3_key.sql`
- 현재 런타임 DB 경로: `raw_documents -> report_raw_details -> report_valuation_facts -> source_documents/signal_events/signal_metrics -> analysis_results/agent_results`
- 큐 핸들러: `services/agent-worker/app/orchestrator/report/tasks.py`
- 현재 Report 큐 작업: `collect_report`, `process_report`, `normalize_report`, `analyze_report`
- 현재 제외/미구현 범위: `embed_report`, RAG retriever, Report Agent
- 현재 남은 연결 작업: 실제 운영 DB row 기반 backfill/runbook 검증과 UI/API 노출 정책 점검
- 전체 스모크 테스트 런북: `docs/runbooks/report-pipeline-smoke-test.md`
- 밸류에이션 재해석 전략: `docs/spec/report-valuation-reinterpretation-strategy.md`
- 과거 담당자 인수인계 문서: `docs/spec/eunjinspec.md`

`docs/spec/eunjinspec.md`는 과거 작업 맥락을 이해하는 데 유용하지만, `report_raw` / `report_signal` 중심의 레거시 경로와 RAG 계획을 많이 설명합니다. 신규 개발은 명시적인 legacy 이전 작업이 아니라면 현재 코드의 canonical 경로를 기준으로 진행합니다.

## 현재 요약

- 구현된 런타임 경로는 `collect_report -> process_report -> normalize_report -> analyze_report`입니다.
- `process_report`는 PDF 다운로드/저장, 텍스트 파싱, valuation fact 저장, `normalize_report` enqueue를 담당합니다.
- `normalize_report`는 Report raw 문서를 `source_documents`, `signal_events`, `signal_metrics`로 승격하고 `analyze_report`를 등록합니다.
- `analyze_report`는 Report 이벤트와 `report_valuation_facts`를 읽어 deterministic 분석 결과를 `analysis_results`, `agent_results`에 저장합니다. (코드상 `ml_infer`를 등록하지만 **#11에서 메타러너는 라이브 미배선이며 REPORT는 끝단 LLM 종합(SYNTHESIZE)에 근거로 합류**한다.)
- `report_valuation_facts`와 valuation summary/scenario band helper, 백테스트 fixture는 구현되어 있습니다.
- `embed_report`, RAG retriever, Report Agent는 현재 코드에 없습니다.
- Report 분석 결과 ID는 `aggregate_ctx.source_analysis_result_ids`를 통해 ML/Aggregator queue 입력으로 전달됩니다.
- Aggregator는 `REPORT`를 최종 `score_breakdown` 근거 소스로 수용하고, Report valuation payload를 `score_breakdown.REPORT.valuation`에 보존합니다. Report는 현재 점수 산정 소스에는 포함하지 않습니다.

## 현재 canonical 흐름

### 1. 수집 스케줄 등록

Endpoint:

```text
POST /internal/schedules/report/collect
```

구현 파일:

- `services/agent-worker/app/api/routes/schedules.py`
- `services/agent-worker/app/orchestrator/report/scheduler.py`

동작:

- active 종목을 조회합니다.
- 종목별로 `collect_report` 작업을 `processing_queue`에 등록합니다.
- `date_start` / `date_end`가 있으면 절대 날짜 범위를 사용합니다.
- 날짜 범위가 없으면 `days_back` 기준으로 수집 범위를 계산합니다.

### 2. `collect_report`

Handler:

```text
ReportCollectTaskHandler
```

구현 파일:

- `services/agent-worker/app/orchestrator/report/tasks.py`
- `services/agent-worker/app/collectors/report/crawler.py`

현재 동작:

- 네이버 금융 리서치 페이지를 크롤링합니다.
- 증권사명으로 리포트를 필터링하지 않고 네이버 금융 리서치 목록의 전체 증권사를 수집 후보로 둡니다.
- 리포트 제목, 증권사명, 발행일, PDF URL 등 메타데이터를 추출합니다.
- `CollectionRepository`를 통해 `raw_documents`와 `report_raw_details`에 저장합니다.
- 저장된 raw 문서마다 `process_report` 작업을 등록합니다.
- 실행 시작과 완료/실패 상태를 `collector_runs`에 기록합니다.
  - `collector_type='REPORT'`
  - `run_mode='batch'`
  - `collected_count`: 크롤러가 반환한 리포트 수
  - `inserted_count`: 저장 후 후속 처리 대상이 된 raw 문서 수
  - `skipped_count`: 날짜 파싱 실패 등으로 저장되지 않은 리포트 수
  - `failed_count`: 수집/저장/queue 등록 중 예외가 난 경우 1

현재 검증:

- `test_report_task_handlers.py`와 `test_report_e2e_pipeline.py`에서 fake DB connection 기반으로 `collect_report → process_report → normalize_report → analyze_report` 흐름을 검증합니다.
- 검증 범위는 raw 문서 저장, PDF 처리 결과 저장, valuation fact 저장, canonical `source_documents`/`signal_events` 승격, Report 분석 결과 저장, 후속 enqueue 동작을 포함합니다.
- 현재 테스트는 `embed_report`, RAG 검색, Report Agent 분석 저장을 검증하지 않습니다. 해당 런타임 경로가 코드에 없기 때문입니다.

### 3. `process_report`

Handler:

```text
ReportProcessTaskHandler
```

현재 동작:

- `report_raw_details`에서 `pdf_url`, `s3_key`, `parsing_status`를 조회합니다.
- 기본 storage backend는 GCS이며, 로컬 테스트에서는 `REPORT_STORAGE_BACKEND=local`로 파일시스템 저장소를 사용할 수 있습니다.
- report storage에 파일이 없으면 원천 PDF URL에서 다운로드한 뒤 선택된 backend에 업로드합니다.
- report storage에 저장된 PDF의 전체 텍스트를 추출합니다.
- 기본값(`REPORT_USE_LLM=false`)에서는 LLM을 호출하지 않고 규칙 기반 fallback으로 목표주가, 원문 의견, 근거 후보를 추출합니다.
  - 목표주가 표기: `목표주가`, `목표가`, `TP`, `Target Price`
  - 가격 단위: `원`, `KRW`, `만원`
  - 의견 표기: `Buy`, `Outperform`, `Marketperform`, `Underperform`, `Hold`, `Neutral`, `Sell` 및 한국어 매수/중립/매도 계열
- `REPORT_USE_LLM=true`일 때만 규칙 기반 후보 텍스트를 LLM에 전달해 파싱 결과를 보강합니다.
- LLM 보강은 `REPORT_LLM_PROVIDER`(`gemini` 또는 `openai`)와 `REPORT_LLM_MODEL` 설정을 사용합니다.
- 파싱 결과를 `report_raw_details`에 갱신합니다.
  - `s3_key`
  - `has_pdf = TRUE`
  - `parsing_status = 'success'`
  - `parsed_at`
  - `investment_opinion`
  - `target_price`
  - `key_rationale`
  - `extracted_text`
- 파싱이 끝나면 `normalize_report` 작업을 등록합니다.

저장소 관련 주의:

- 현재 canonical queue 경로의 기본 backend는 GCS입니다.
- 로컬 테스트 파일 저장은 `REPORT_STORAGE_BACKEND=local`과 `REPORT_LOCAL_STORAGE_DIR`로 활성화합니다. 기본 경로는 저장소 루트 기준 `data/report-storage`입니다.
- local backend는 object key와 같은 상대 경로로 PDF를 저장하며, `..` 같은 경로 이탈 key는 거부합니다.
- 신규 PDF object key는 `reports/{stock_code}/{publish_date}_{firm_slug}_{source_hash8}.pdf` 형식을 사용합니다.
  - 예: `reports/005930/20260624_hana_abcdef12.pdf`
  - 같은 종목, 발행일, 증권사, 리포트 유형이 겹쳐도 source hash prefix로 충돌을 줄입니다.
- `pdf_downloader.py`, `run_parser.py` 같은 과거 CLI 경로는 `data/reports/` 아래에 PDF를 저장할 수 있습니다.
- queue handler는 `REPORT_STORAGE_BACKEND` 값에 따라 GCS 또는 local storage client를 사용합니다.
- DB 컬럼명은 아직 `s3_key`이지만, 현재 구현에서는 선택된 storage backend의 object key로 사용합니다.

저작권과 보존 관련 주의:

- 사용자-facing 응답에는 PDF 원문이나 긴 verbatim 청크를 노출하지 않습니다.
- 현재 런타임은 PDF 전체 원문이나 긴 청크를 사용자-facing 응답으로 내보내지 않습니다.
- `report_chunks` 기반 내부 RAG 검색은 현재 런타임에 연결되어 있지 않습니다. RAG를 복구할 경우에도 사용자-facing 응답에는 원문 PDF 또는 긴 verbatim 청크를 노출하지 않는 원칙을 유지해야 합니다.
- 밸류에이션 재해석 확장에서는 원문 문장을 그대로 저장하는 대신 구조화된 fact와 패러프레이즈된 thesis를 별도 저장하는 방향을 우선합니다.

### 4. `normalize_report`

Handler:

```text
ReportNormalizeTaskHandler
```

현재 동작:

- `parsing_status = 'success'`인 `report_raw_details`와 `raw_documents`를 조회합니다.
- 리포트 raw 문서를 `source_documents`로 승격합니다.
- 리포트 1건마다 `signal_events`를 생성합니다.
  - `source_type = 'REPORT'`
  - `event_type = 'report_published'`
  - 증권사 원문 의견은 데이터 방향성으로만 매핑합니다.
  - 알 수 없는 의견 값은 `signal_direction='unknown'`, `needs_review=true`로 둡니다.
- 목표가, 이전 목표가, 발간 시점 현재가, 상승여력 원천 값이 있으면 `signal_metrics`에 저장합니다.
- source trace 검증 로그를 `validation_logs`에 남깁니다.
- `analyze_report` 작업을 등록합니다.

### 5. `analyze_report`

Handler:

```text
ReportAnalyzeTaskHandler
```

현재 동작:

- `source_signal_event_ids`가 없으면 분석을 건너뜁니다.
- `signal_events`, `source_documents`, `report_valuation_facts`를 조인해 Report 이벤트와 밸류에이션 fact를 읽습니다.
- LLM을 호출하지 않고 결정론 규칙으로 데이터 방향성, 점수, 검토 필요 여부를 계산합니다.
- `analysis_results`에 Report 분석 대표 row를 저장합니다.
- `agent_results.method_detail.report_quant.valuation`에 목표가, EPS, 적용 배수, 내재 배수, 피어 그룹, extraction source, needs_review를 저장합니다.
- `AGGREGATE_SIGNAL` 작업을 **직접 등록**하고 `aggregate_ctx.source_analysis_result_ids`에 Report `analysis_result_id`를 담아 후속 Aggregator queue 체인으로 넘깁니다. **(C안 Phase 1, #585)** 과거 경유하던 변동성 ML 채널(`ML_INFER`/`META_COMBINE`)은 제거됐고 `report/tasks.py`가 `enqueue_aggregate` 로 바로 넘긴다. REPORT는 점수가 아니라 끝단 LLM 종합(SYNTHESIZE)에 합류하는 근거이며, 방향은 투자의견 컨센서스 기반 결정론으로 산출한다.
- 사용자-facing 최종 발행은 직접 하지 않고, 후속 Aggregator/gate 경로에 맡깁니다.

현재 빈틈:

- Report 단독 또는 다른 소스와의 동시 운영 시 같은 날짜/스케줄에서 어떤 기준으로 함께 묶을지 운영 정책을 더 정리해야 합니다.
- Report Agent 합성이나 RAG Top-K 근거 검색은 제품 범위에서 제외되어 있습니다.

### 6. 제외된 범위: RAG/Report Agent 런타임

아래 경로는 현재 코드에 없습니다.

- `embed_report` task type
- `ReportEmbedTaskHandler`
- `services/agent-worker/app/analyzers/report/rag_retriever.py`
- `services/agent-worker/app/agents/report/agent.py`
- `Report Agent`가 생성하는 RAG 기반 Report 전용 `analysis_results`, `agent_results`
- RAG `evidence_chunks`를 Aggregator까지 전달하는 런타임 체인

Report RAG는 복구 계획이 없습니다. 신규 개발은 `report_valuation_facts` 기반 deterministic 분석 경로만 확장합니다.

현재 빈틈:

- Report 분석은 `final_signals`를 직접 쓰지 않습니다.
- 사용자-facing 최종 데이터 방향성 발행 여부는 Aggregator와 후속 gate가 결정합니다.
- `process_report`는 밸류에이션 재해석 전략의 `forward_eps_est`, `applied_multiple`, `implied_multiple`, `peer_group`를 규칙 기반으로 구조화하고, LLM 설정이 활성화된 경우 `category_tag`, `rerating_thesis`, `methodology`를 보강합니다.
- valuation helper는 `report_valuation_facts`를 읽어 내재 배수 평균·중앙값·분산, 적용 배수 대비 gap, 피어 그룹 빈도, `needs_review` 비율, `scenario_band`를 계산할 수 있습니다.
- 현재 `analyze_report`는 valuation payload를 `agent_results.method_detail.report_quant.valuation`에 저장합니다.
- Aggregator는 Report `method_detail.report_quant.valuation`이 들어오면 `score_breakdown.REPORT.valuation`과 caution evidence로 전달합니다. Report score는 breakdown에 보존하지만 현재 최종 점수 산정에는 포함하지 않습니다.
- `scenario_band`는 내재 배수 중앙값을 base로 두고 분산의 제곱근 범위를 low/high로 계산하는 내부 구조화 값입니다. 투자 행동 제안으로 노출하지 않습니다.
- LLM 설정이 비활성화되었거나 model/key/provider가 불완전하면 `process_report`의 PDF 파싱 보강은 규칙 기반 fallback을 사용합니다.
  - 수치 값은 규칙 기반 추출 또는 DB row만 사용합니다.
  - LLM JSON 오류, timeout, 금지 표현 감지 시 `rules_fallback`, `needs_review=true`로 저장합니다.

## 레거시 경로

아래 런타임 경로는 canonical queue 기반 흐름으로 대체되어 제거되었습니다. 신규 개발은 현재 구현된 `collect_report -> process_report -> normalize_report -> analyze_report` 경로를 기준으로 진행합니다. `embed_report`/RAG 단계는 제품 범위에서 제외합니다.

### `/agents/report`

제거된 내용:

- `services/agent-worker/app/api/routes/report.py` 삭제
- `services/agent-worker/app/collectors/report/collector.py` 삭제
- `services/agent-worker/app/analyzers/report/analyzer.py` 삭제
- `services/agent-worker/app/main.py`에서 `/agents/report`, `/agents/analyze` 라우터 등록 제거
- 허용 종목 코드가 하드코딩되어 있습니다.
- 오래된 `ReportAnalyzer`가 `report_signal`에 결과를 저장합니다.
- 일부 로직은 `price_raw`, `report_chunks.stock_code`처럼 현재 canonical schema와 맞지 않는 테이블 또는 컬럼을 가정합니다.

### `vector_store.py`

제거된 내용:

- `services/agent-worker/app/collectors/report/parsers/vector_store.py` 삭제
- 과거 `parsed_reports.json` 기반 로컬 배치 흐름을 위해 만들어졌습니다.
- `report_raw`에 데이터를 저장합니다.
- 현재 `report_chunks` canonical schema에는 없는 구식 메타데이터 컬럼을 insert하려는 경로가 있습니다.

## DB 테이블

### Canonical 테이블

- `raw_documents`: source 공통 메타데이터
- `report_raw_details`: 증권사 리포트 상세, PDF 상태, 파싱 필드
- `report_chunks`: RAG 복구 후보 스키마입니다. 현재 Report 런타임에서는 청크/embedding을 저장하지 않습니다.
- `report_valuation_facts`: 리포트별 목표가 산정 fact, EPS, 적용 배수, 내재 배수, 피어 그룹
- `processing_queue`: queue 작업 상태
- `analysis_results`: Report 분석 대표 row를 저장합니다.
- `agent_results`: Report deterministic 분석 상세와 valuation payload를 저장합니다.

### 밸류에이션 fact

- `029_report_valuation_facts.sql`에서 `report_valuation_facts` 테이블을 추가했습니다.
- `process_report`는 PDF 파싱 결과에서 `target_price`, `forward_eps_est`, `eps_fy`, `methodology`, `applied_multiple`, `implied_multiple`, `peer_group`를 구조화해 저장합니다.
- `implied_multiple = target_price / forward_eps_est` 계산은 결정론 코드가 담당합니다.
- `REPORT_USE_LLM=true`이고 provider/model/API key가 모두 설정되면 LLM이 `methodology`, `category_tag`, `rerating_thesis`만 보강합니다. 목표가, EPS, 적용 배수, 내재 배수는 LLM 결과를 사용하지 않습니다.
- LLM JSON 오류, timeout, 금지 표현 감지 시 `extraction_source='rules_fallback'`, `needs_review=TRUE`로 저장합니다.
- 추출 실패나 핵심 값 결측 시 `needs_review = TRUE`로 저장해 추가 확인 필요 상태를 남깁니다.
- valuation helper는 `report_valuation_facts`를 읽어 valuation summary를 만들고, `needs_review` 비율이 높으면 `data_status='partial'`과 `valuation_review_required` risk flag를 계산합니다.
- valuation summary에는 내부용 `scenario_band.low_multiple/base_multiple/high_multiple`, `dispersion_level`, `confidence_note`가 포함됩니다.
- 현재 `analyze_report`가 valuation payload를 `agent_results.method_detail.report_quant.valuation`에 저장합니다.

### Legacy 테이블

- `report_raw`
- `report_signal`

두 테이블은 호환성을 위해 baseline에 남아 있지만, 신규 코드에서는 참조하지 않는 것을 원칙으로 합니다.

## 운영 실행 예시

수집부터 최종 `final_signals.score_breakdown.REPORT` 확인까지 한 번에 검증하려면 `docs/runbooks/report-pipeline-smoke-test.md`를 우선 사용합니다. 아래 예시는 worker가 실행 중일 때 가능한 local API 호출 순서의 축약본입니다.

Gemini LLM 보강까지 켜서 PDF 파싱을 확인하려면 `docs/spec/report-gemini-pdf-parsing-dev-guide.md`를 먼저 참고합니다.

```powershell
# 1. 리포트 수집 작업 등록
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/report/collect" `
  -ContentType "application/json" `
  -Body '{"date_start":"2026-06-01","date_end":"2026-06-24","limit":10,"priority":"batch"}'

# 2. 수집 작업 실행
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/collect_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'

# 2-1. Report collector_runs 확인
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8011/internal/stats/collectors/runs?collector_type=REPORT&limit=10"

# 3. PDF 다운로드와 파싱 작업 실행
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/process_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'

# 4. 리포트 canonical 정규화 작업 실행
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/normalize_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'

# 5. 리포트 deterministic 분석 결과 저장
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/analyze_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'

# embed_report/RAG 경로는 현재 코드에 없으므로 실행하지 않습니다.
```

주의:

- `/internal/schedules/report/collect`는 현재 단일 `stock_code` 필드를 받지 않습니다.
- 단일 종목만 수집하려면 `/internal/tasks/collect_report/enqueue`로 직접 등록합니다.

단일 종목 enqueue 예시:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/tasks/collect_report/enqueue" `
  -ContentType "application/json" `
  -Body '{"stock_id":1,"priority":"batch","task_context":{"stock_code":"005930","date_start":"2026-06-01","date_end":"2026-06-24","max_pages":20}}'
```

## 인수인계 체크리스트

후속 개발 전에 아래 결정을 먼저 내리는 것이 좋습니다.

0. 밸류에이션 재해석 확장
   - `report_valuation_facts` 스키마, extractor MVP, LLM 기반 methodology/category/thesis 보강, valuation analyzer MVP, scenario band MVP, Aggregator 전달, 백테스트 fixture 확인/미확인 사례, 수집 파이프라인 형태 샘플 변환은 구현되었습니다.
   - Aggregator는 Report valuation payload를 최종 `score_breakdown.REPORT`에 수용합니다. 남은 작업은 실제 운영 DB row 기반 backfill 검증과 API/UI 노출 정책 점검입니다.
   - 실제 운영 DB row에서 fixture 후보를 추출하고, normalize/analyze backfill 결과와 비교하는 작업은 계속 유효합니다.
   - PDF 원문과 긴 청크를 사용자에게 노출하지 않고, valuation 전략용 결과는 구조화 fact 중심으로 저장합니다.
1. 저장 backend
   - canonical queue 경로의 기본 backend는 GCS입니다. 개발과 테스트용 local storage adapter도 구현되어 있으므로, 운영 환경에서는 GCS bucket/권한을 확정하고 로컬 환경에서는 `REPORT_STORAGE_BACKEND=local` 사용 여부를 정하면 됩니다.
2. 정규화 경로
   - Report는 `normalize_report`에서 `source_documents`, `signal_events`, `signal_metrics`를 만듭니다. 후속 작업은 기존 데이터 backfill과 운영 runbook 정리입니다.
3. LLM 연결
   - `REPORT_USE_LLM`, provider, model, timeout, API key 설정은 PDF 파싱 보강에 연결되어 있습니다. `ReportAnalyzeTaskHandler`는 LLM을 호출하지 않고 정규화된 DB 데이터만 읽습니다.
   - 운영 환경에서 provider/model/key 값을 확정하고 parser fallback 품질을 점검합니다.
4. Aggregator 통합
   - Report 런타임은 `analysis_results`, `agent_results`를 만들고 `AGGREGATE_SIGNAL` 입력 queue까지 합류합니다.
   - Aggregator는 `REPORT`를 지원 소스로 수용하고 `score_breakdown.REPORT.valuation`을 보존합니다.
   - 후속 작업은 DART, PRICE, ALTERNATIVE와 같은 날짜/스케줄에서 어떻게 함께 묶을지 운영 정책을 정하는 것입니다.
5. 레거시 정리
   - `/agents/report`, `ReportAnalyzer`, `ReportCollector`, `vector_store.py` 런타임 경로는 제거되었습니다.
   - `report_raw`, `report_signal` 테이블은 호환성을 위해 DB에 남아 있지만 신규 코드에서 참조하지 않습니다.
6. collector 실행 로그
   - `collect_report`는 `collector_runs` 생성과 완료/실패 집계를 기록합니다.
   - Report 저장은 `CollectionRepository` 기반으로 정리되어 `collector_runs`와 raw 문서 추적이 이어집니다.
   - 성공/실패 시 agent-worker 로그에 `report_collection_summary` 이벤트를 남깁니다.
   - 로그 payload에는 `collected_reports`, `saved_reports`, `inserted_reports`, `duplicate_reports`, `invalid_date_reports`, `missing_pdf_reports`, `enqueued_reports`, `skip_reasons`가 포함됩니다.
7. 테스트 범위
   - storage backend, queue chaining, Report 파싱/정규화 저장은 unit/integration 테스트로 유지합니다.
   - fake DB connection 기반 Report E2E queue pipeline으로 canonical 링크와 후속 enqueue를 회귀 검증합니다.

## 현재 테스트 범위

현재 집중 테스트가 다루는 영역입니다.

- Report task handler
- Report collection scheduler
- Report schedule route
- Report LLM wiring
- Report PDF parser fallback과 LLM 응답 파싱
- Report valuation backtest fixture
  - 확인/미확인 사례 fixture 로드
  - 수집 파이프라인 형태 샘플을 백테스트 case로 변환
  - valuation summary와 사후 관찰 일치/충돌 수 기반 기대 결과 검증
- Report E2E queue pipeline
  - fake connection 기반 직접 핸들러 체인 검증
  - `QueueTaskRunner` 기반 claim/mark_success와 `aggregate_signal` enqueue 검증
- data-access의 Report chunk, raw detail, collection repository 메서드

유용한 테스트 명령:

```powershell
uv run pytest services\agent-worker\tests\test_report_e2e_pipeline.py services\agent-worker\tests\test_report_task_handlers.py services\agent-worker\tests\test_report_llm_wiring.py services\agent-worker\tests\test_report_scheduler.py services\agent-worker\tests\test_report_schedule_route.py services\agent-worker\tests\test_report_parser_fallback.py -q

uv run pytest services\agent-worker\tests\test_report_valuation_backtest_fixtures.py services\agent-worker\tests\test_report_valuation_analyzer.py -q

uv run pytest packages\data-access\tests\test_collection_repository.py packages\data-access\tests\test_report_chunk_repository.py packages\data-access\tests\test_raw_detail_repository.py -q
```

## normalize_report backfill runbook

기존에 `process_report`까지 완료되어 `report_raw_details.parsing_status = 'success'` 상태지만 아직 `source_documents`로 승격되지 않은 리포트는 운영 backfill API로 `normalize_report` 작업을 다시 등록할 수 있습니다. 이 작업은 원천 데이터를 새로 수집하지 않고 canonical 정규화 경로만 복구합니다.

기본값은 dry-run입니다. 먼저 후보 건수와 raw 문서 ID를 확인합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/report/normalize-backfill" `
  -ContentType "application/json" `
  -Body '{"limit":100}'
```

특정 종목만 확인하려면 `stock_code`를 지정합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/report/normalize-backfill" `
  -ContentType "application/json" `
  -Body '{"stock_code":"005930","limit":100}'
```

후보가 맞으면 `dry_run=false`로 `normalize_report` 작업을 등록합니다. 등록된 작업은 기존 큐 실행 API로 처리합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/report/normalize-backfill" `
  -ContentType "application/json" `
  -Body '{"limit":100,"dry_run":false,"priority":"batch"}'

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/normalize_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":100}'
```

응답 필드:

- `candidate_count`: backfill 후보 raw 문서 수
- `scheduled_count`: backfill 대상 후보 중 task id가 확보된 수
- `enqueued_count`: 새로 `processing_queue`에 등록된 `normalize_report` 작업 수
- `reused_count`: 이미 pending/running/retrying 상태라 dedupe로 재사용한 작업 수

주의:

- 후보 조건은 `parsing_status = 'success'`이고 `source_documents(source_type='REPORT')`가 아직 없는 raw 문서입니다.
- enqueue 컨텍스트는 `process_report`가 자동 등록하는 `normalize_report`와 동일하게 유지해 pending/running/retrying 작업 dedupe가 동작하게 합니다.
- `reused_count`가 0보다 크면 새 작업을 중복 생성하지 않고 기존 열린 작업 ID를 재사용한 것입니다.
- 이 backfill은 사용자-facing 데이터 방향성, 근거, 소스 간 일치도 계산의 원천 후보를 canonical 경로로 승격하는 작업이며, 매수/매도/보유 추천을 생성하지 않습니다.

