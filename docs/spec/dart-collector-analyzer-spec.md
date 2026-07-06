# Signal Alpha DART 수집기·분석기 상세 스펙

> 기준일: 2026-06-16
> 대상: `services/agent-worker`의 DART 수집, 정규화, 분석 흐름
> 목적: DART 관련 개발을 이어갈 때 코드와 DB, API, 운영 방식을 한 번에 파악하기 위한 상세 스펙
>
> ⚠️ **현재 계약:** DART는 판정/점수/ML 라벨 채널이 아니라 근거·커버리지 소스다. 분석 결과는
> `direction="unknown"`, `score=0`, `data_status="no_signal"`로 저장되어 `score_breakdown.DART`와
> 끝단 LLM 종합(SYNTHESIZE)에 근거로 남지만 숫자 `final_score` 평균에는 들어가지 않는다.
> `backfill_dart_labels` 이벤트스터디 라벨 백필과 DART 소스 ML 채널은 운영 경로에서 제거되었다.
> **수집·정규화(§5·§6)와 DB 적재(§4)는 그대로 유효·재사용**한다.

---

## 1. 범위와 원칙

DART 모듈은 OpenDART 공시 및 ownership 데이터를 수집하고, 원천을 표준 이벤트로 정규화한 뒤, DART analysis flow를 통해 정형 피처와 근거를 `analysis_results`와 `agent_results`에 저장한다. 현재 flow는 LangGraph 의존성을 사용하지 않으며, class 이름과 `graph_nodes` 메타데이터만 호환 목적으로 유지한다.

현재 DART 흐름은 공시 경로와 ownership 경로로 분리된다.

```text
collect_dart
  -> raw_documents + dart_raw_details 저장
  -> normalize_dart 큐 등록
  -> source_documents + signal_events + signal_metrics 생성
  -> analyze_dart 큐 등록
  -> analysis_results + agent_results 저장
  -> aggregate_signal 큐 등록
  -> final_signals 생성

collect_dart_ownership
  -> dart_ownership_events 저장
  -> normalize_dart_ownership 큐 등록
  -> source_documents + signal_events + signal_metrics 생성
  -> analyze_dart 큐 등록
```

핵심 원칙은 다음과 같다.

- 수집기는 OpenDART API 호출과 원천 저장까지만 담당한다.
- 정규화기는 원문을 `source_documents`, `signal_events`, `signal_metrics`로 변환한다.
- 분석기는 `signal_events`만 읽어 정형 피처, 근거 요약, 리스크 플래그를 만든다. DART 자체 방향성/점수 verdict는 내지 않는다.
- `analyze_dart`는 입력 검증, 분석 실행, 출력 검증 흐름을 거쳐 `agent_results.method_detail`에 provenance를 남긴다.
- LLM은 기본값으로 꺼져 있으며, 활성화하더라도 고임팩트 공시의 근거(summary/key facts/risk flags) 추출에만 선택 적용한다.
- 투자 추천 문구를 만들지 않고, 공시 기반 정보 방향성과 추가 검토 필요 여부만 제공한다.

---

## 2. 주요 코드 위치

| 영역 | 위치 | 역할 |
|---|---|---|
| DART 수집 클라이언트 | `services/agent-worker/app/collectors/dart/disclosure.py` | `/list.json`, `/document.xml` 호출, ZIP/XML 텍스트 추출 |
| corp code 동기화 | `services/agent-worker/app/collectors/dart/corp_codes.py` | `/corpCode.xml` ZIP/XML 다운로드 및 `dart_corp_codes` 적재 |
| DART 작업 핸들러 | `services/agent-worker/app/orchestrator/dart/tasks.py` | `collect_dart`, `normalize_dart`, `analyze_dart` 실행 |
| DART 스케줄러 | `services/agent-worker/app/orchestrator/dart/scheduler.py` | 타깃 종목의 수집 작업 일괄 등록 |
| 큐 핸들러 등록 | `services/agent-worker/app/orchestrator/queue/handlers.py` | task type별 핸들러 매핑 |
| 분류 룰 | `services/agent-worker/app/analyzers/dart/rules.py` | 공시 제목 기반 event type, direction, impact 분류 |
| features-only 분석 | `services/agent-worker/app/analyzers/dart/source_result.py` | 이벤트 묶음의 정형 피처, 근거 요약, 리스크 플래그 산출 |
| LLM 근거 추출 | `services/agent-worker/app/agents/dart/evidence.py` | Gemini/OpenAI 선택 근거 추출, JSON 검증, fallback |
| DART Source Agent | `services/agent-worker/app/agents/dart/agent.py` | Source Agent 계약 기반 features-only 결과와 선택적 LLM 근거 생성 |
| DART analysis flow | `services/agent-worker/app/agents/dart/graph.py` | 입력 검증, DART Agent 호출, 출력 메타데이터 보강 |
| 재무 지표 추출 | `services/agent-worker/app/analyzers/dart/financials.py` | 공시 텍스트에서 매출/영업이익/순이익 수치 추출 |
| API 라우터 | `services/agent-worker/app/api/routes/dart.py` | DART 조회, E2E 실행, corp code sync |

---

## 3. 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DART_API_KEY` | 없음 | OpenDART API 키. 수집과 corp code sync에 필수 |
| `DART_BASE_URL` | `https://opendart.fss.or.kr/api` | OpenDART API base URL |
| `DART_TIMEOUT_SECONDS` | `10` | OpenDART HTTP timeout |
| `DART_PAGE_SIZE` | `100` | `/list.json` page_count |
| `DART_FETCH_DOCUMENTS` | `true` | `document.xml` ZIP/XML 원문 다운로드 여부 |
| `DART_MAX_RETRIES` | `2` | 재시도 가능한 DART 오류의 최대 재시도 횟수 |
| `DART_RETRY_BACKOFF_SECONDS` | `0.5` | 재시도 backoff 시작값 |
| `DART_USE_LLM` | `false` | DART LLM 근거 추출 사용 여부(판정/점수 아님) |
| `DART_LLM_HIGH_IMPACT_ONLY` | `true` | high impact 이벤트에만 LLM 적용 |
| `DART_LLM_PROVIDER` | `gemini` | `gemini` 또는 `openai` |
| `DART_LLM_MODEL` | 없음 | 사용할 LLM 모델명 |
| `DART_LLM_TIMEOUT_SECONDS` | `20` | LLM 호출 timeout |
| `GEMINI_API_KEY` | 없음 | Gemini 사용 시 필요 |
| `OPENAI_API_KEY` | 없음 | OpenAI 사용 시 필요 |

---

## 4. DB 테이블 책임

### 수집 계층

| 테이블 | 책임 |
|---|---|
| `dart_corp_codes` | ticker와 OpenDART `corp_code` 매핑 |
| `dart_collection_states` | 종목별 마지막 수집 구간과 마지막 접수번호 |
| `collector_runs` | DART 수집 실행 단위 로그 |
| `raw_documents` | DART 원천 문서 공통 헤더. `source_type='DART'`, `external_id=receipt_no` |
| `dart_raw_details` | DART 전용 상세 필드. 접수번호, 공시명, 정정 여부, 원문 텍스트 payload |
| `processing_queue` | `normalize_dart`, `analyze_dart`, `aggregate_signal` 후속 작업 |

### 정규화·분석 계층

| 테이블 | 책임 |
|---|---|
| `source_documents` | 원천 공시를 분석 가능한 표준 문서로 등록 |
| `signal_events` | 공시별 이벤트, 방향성, 영향도, evidence 텍스트 |
| `signal_metrics` | 공시 수, 매출/영업이익/순이익 등 수치 지표 |
| `validation_logs` | 정규화 trace 및 검증 로그 |
| `analysis_results` | DART 분석 실행 결과 헤더 |
| `agent_results` | DART agent의 no-signal 중립 저장값, 상세 JSON, 선택적 LLM 근거 메타데이터 |

현재 `analyze_dart`는 `final_signals`를 직접 발행하지 않는다. 성공 시 `aggregate_signal`을
enqueue하고, Aggregator가 `final_signals`를 생성한다.

---

## 5. collect_dart 스펙

### 입력

`processing_queue`에 다음 형태로 등록한다.

```json
{
  "stock_id": 1,
  "task_type": "collect_dart",
  "priority": "batch",
  "task_context": {
    "stock_code": "005930",
    "bgn_de": "20260601",
    "end_de": "20260612",
    "force_reprocess": false
  }
}
```

필수값은 `stock_id`, `task_context.stock_code`다.

선택값:

- `bgn_de`: OpenDART 시작일. 생략하면 `dart_collection_states.last_end_de + 1일`을 사용한다.
- `end_de`: OpenDART 종료일. 생략하면 실행일을 사용한다.
- `force_reprocess`: 이미 수집된 `receipt_no`도 정규화 큐에 다시 넣을지 여부.

### 수집 흐름

1. `dart_corp_codes`에서 `stock_code`에 대응하는 `corp_code`를 조회한다.
2. `bgn_de`, `end_de` 수집 구간을 결정한다.
3. OpenDART `/list.json` 1페이지를 조회한다.
4. 응답의 `total_page` 기준으로 나머지 페이지를 순차 조회한다.
5. 각 공시에 대해 `DART_FETCH_DOCUMENTS=true`이면 `/document.xml`을 호출한다.
6. ZIP 내부 XML을 읽고 `ElementTree.itertext()`로 텍스트를 추출한다.
7. 공시별 `RawEvidence(source='DART')`를 만든다.
8. `raw_documents`, `dart_raw_details`에 upsert한다.
9. 신규 또는 `force_reprocess=true`인 원문에 대해 `normalize_dart`를 큐에 등록한다.
10. `dart_collection_states`를 갱신한다.

### RawEvidence 주요 필드

```json
{
  "source": "DART",
  "stock_code": "005930",
  "title": "분기보고서",
  "content": "document.xml에서 추출한 텍스트 또는 공시명",
  "published_at": "2026-06-12",
  "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...",
  "metadata": {
    "receipt_no": "20260612000123",
    "external_id": "20260612000123",
    "corp_code": "00126380",
    "report_name": "분기보고서",
    "disclosure_type": "quarter_report",
    "priority": "batch",
    "is_correction": false,
    "original_receipt_no": null,
    "source_name": "OpenDART",
    "document_fetch_status": "success",
    "document_text": "추출 텍스트",
    "document_files": []
  }
}
```

### 중복·정정 정책

- 중복 기준은 `raw_documents(source_type, external_id)`이며, DART의 `external_id`는 `receipt_no`다.
- 동일 접수번호 재수집은 기존 `raw_documents` row를 갱신한다.
- 정정 공시는 접수번호가 다르므로 별도 원문과 별도 이벤트로 저장한다.
- OpenDART payload에 원공시 접수번호가 있으면 `dart_raw_details.original_receipt_no`에 저장한다.
- 정정 공시는 `priority='immediate'`, `needs_review=true` 성격으로 처리한다.

### 오류 정책

| OpenDART status / 오류 | 분류 | 재시도 |
|---|---|---|
| `000` | 성공 | 아니오 |
| `013` | 데이터 없음 | 아니오 |
| `020` | rate limit | 예 |
| `800`, `900` | 서비스 오류 | 예 |
| HTTP 5xx, 429, timeout, URL 오류 | network | 예 |
| `010`, `011`, `012`, `901` | 인증 오류 | 아니오 |
| `014`, `021`, `100`, `101` | 요청 오류 | 아니오 |
| ZIP 파싱 실패 | parse | 아니오 |

`/document.xml` 실패는 전체 공시 수집을 실패시키지 않는다. 해당 공시 metadata에 `document_fetch_status='failed'`, `document_error_category`, `document_error_retryable` 등을 남기고 계속 진행한다.

---

## 6. normalize_dart 스펙

### 입력

`collect_dart`가 등록한 `normalize_dart` 작업은 보통 다음 형태다.

```json
{
  "task_type": "normalize_dart",
  "stock_id": 1,
  "source_raw_ids": [101],
  "task_context": {
    "stock_code": "005930",
    "source_type": "DART"
  }
}
```

### 정규화 흐름

1. `source_raw_ids`로 `dart_raw_details`와 `raw_documents`를 join 조회한다.
2. 공시명과 정정 여부를 `classify_dart_report()`로 분류한다.
3. `source_documents`에 공식 문서로 upsert한다.
4. `signal_events`에 공시 이벤트를 upsert한다.
5. `signal_metrics`에 `dart_disclosure_count=1`을 기록한다.
6. 공시 텍스트에서 재무 수치를 추출해 `signal_metrics`에 추가한다.
7. `validation_logs`에 source trace 로그를 남긴다.
8. 이벤트별로 `analyze_dart` 작업을 등록한다.

### 공시 분류 룰

| 조건 | event_type | signal_direction | impact_level | needs_review |
|---|---|---|---|---|
| 정정, correction, amendment | `correction` | `neutral` | `low` | true |
| 주요사항보고서 | `material_event` | `mixed` | `high` | false |
| 임원 + 주요주주 | `insider_ownership` | `neutral` | `low` | false |
| 사업보고서, 반기보고서, 분기보고서 | `periodic_report` | `neutral` | `medium` | false |
| 기업지배구조보고서 | `governance_report` | `neutral` | `medium` | false |
| 기타 | `dart_disclosure` | `unknown` | `low` | true |

이벤트 중복 기준은 다음 해시다.

```text
sha256("DART|{stock_code}|{receipt_no}|{report_name}")
```

### 재무 지표 추출

`extract_dart_financial_metrics()`는 공시 텍스트에서 다음 지표를 정규식 기반으로 찾는다.

- `dart_revenue`
- `dart_operating_profit`
- `dart_net_income`

단위는 텍스트에서 감지한 `조원`, `억원`, `백만원`, `million krw`, `billion krw` 등을 사용한다. 추출 실패는 정상이며, 이 경우 재무 metric 없이 공시 이벤트만 생성한다.

---

## 7. analyze_dart 스펙

### 입력

`normalize_dart`가 등록한 `analyze_dart` 작업은 보통 다음 형태다.

```json
{
  "task_type": "analyze_dart",
  "stock_id": 1,
  "source_signal_event_ids": [201],
  "task_context": {
    "stock_code": "005930",
    "source_type": "DART",
    "run_key": "DART_EVENT_201"
  }
}
```

`source_signal_event_ids`가 없으면 분석은 실행하지 않고 `skipped_reason='source_signal_event_ids_required'`를 반환한다.

### DART analysis flow

`DartAnalyzeTaskHandler`는 DB에서 `signal_events`를 조회한 뒤 `SourceAgentInput`을 만들고 `DartAnalysisGraphAgent`를 호출한다. 현재 구현은 LangGraph 런타임 의존성 없이 다음 단계를 직접 수행하며, `graph`/`graph_nodes` 메타데이터 이름만 저장 호환을 위해 유지한다.

```text
validate_input
  -> analyze
  -> validate_output
```

| node | 책임 |
|---|---|
| `validate_input` | `source='DART'`, `stock_code`, events 입력을 검증한다. 실패 시 `data_status='failed'`, `analysis_source='graph_validation'` 결과를 반환한다. |
| `analyze` | `DartAnalysisAgent`를 호출해 features-only 분석과 선택적 LLM 근거 추출을 실행한다. |
| `validate_output` | `method_detail.graph='dart_analysis_v1'`, `method_detail.graph_nodes`를 추가한다. |

### features-only 분석

`build_dart_analysis_result()`는 이벤트 목록을 기준으로 다음 값을 산출한다.

| 산출값 | 설명 |
|---|---|
| `direction` | 항상 `unknown` |
| `score` | 항상 `0.0`; DART는 숫자 점수 평균에서 제외 |
| `summary` | 공식 공시/ownership 이벤트 수와 근거 사용 목적을 포함한 요약 |
| `risk_flags` | 정정, 불확실 방향성, 검토 필요 등 |
| `method_detail` | source, data_status=`no_signal`, event_count, direction_counts, events, derived_features |
| `needs_review` | risk flag가 있거나 선택적 LLM 근거 추출이 review 필요를 반환하면 true |

impact weight는 `high=3`, `medium=2`, `low=1`이며 `derived_features.impact_weighted_count` 같은 근거 피처로만 보존된다. `backfill_dart_labels` 기반 이벤트스터디 라벨 백필은 더 이상 운영 큐 경로에 없다.

### 선택적 LLM 근거 추출

LLM 근거 추출은 다음 조건을 만족할 때만 시도한다. 성공해도 DART 방향성/점수 verdict는 만들지 않는다.

- `DART_USE_LLM=true`
- provider별 API key와 model이 설정됨
- `should_use_dart_llm()`이 true
- `DART_LLM_HIGH_IMPACT_ONLY=true`이면 high impact 이벤트가 포함됨

지원 provider:

- Gemini: `DART_LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `DART_LLM_MODEL`
- OpenAI: `DART_LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `DART_LLM_MODEL`

LLM 응답은 strict JSON으로 검증한다. 다음 상황에서는 rule 결과로 fallback한다.

- timeout 또는 API 오류
- JSON 파싱 실패
- 필수 필드 누락
- confidence 범위 오류 또는 필수 근거 필드 오류
- 투자 추천/매수/매도/보유 권유성 문구 감지

LLM 성공 시 `agent_results.method_detail`에는 features-only detail에 더해 다음 값이 들어간다.

- `llm_evidence.summary`
- `llm_evidence.key_facts`
- `llm_evidence.risk_flags`
- `llm_evidence.confidence`
- `graph='dart_analysis_v1'`
- `graph_nodes=['validate_input', 'analyze', 'validate_output']`

fallback 시 `analysis_source='features'`는 유지되고, `llm_error`가 provenance로 저장된다.

### 저장 결과

`analyze_dart`는 다음을 upsert한다.

| 테이블 | 주요 값 |
|---|---|
| `analysis_results` | `analysis_mode='dart_only'`, `run_key`, `base_score`, `source_signal_event_ids`, `warning`, `version` |
| `agent_results` | `debate_method='D-1'`, `method_score`, `method_signal`, `method_detail`, `reliability_score=90`, `evidence_quality`, `llm_model`, `prompt_ver` |

`DartAnalysisAgent` 출력의 `score`는 현재 항상 `0.0`이다. 기존 DB 컬럼 `analysis_results.base_score`와 `agent_results.method_score`는 0~100 CHECK 제약을 유지하므로 저장 시 `(score + 1) * 50`으로 변환되어 50.0으로 기록된다. 이 값은 DART의 판정 점수가 아니라 no-signal 중립 저장값이다.

`analysis_date`는 이벤트 날짜 중 최신값을 사용한다. 이벤트 날짜가 없으면 task context의 `analysis_date` 또는 실행일을 사용한다.

`agent_results.method_detail`에는 DART analysis flow 실행 경로를 확인할 수 있도록 `graph`와 `graph_nodes`가 포함된다.

---

## 8. API 스펙

### corp code 동기화

```http
POST /internal/dart/corp-codes/sync
```

OpenDART `/corpCode.xml`을 조회해 `dart_corp_codes`를 갱신한다.

### 수집 작업 등록

```http
POST /internal/tasks/collect_dart/enqueue
Content-Type: application/json
X-Internal-Token: <INTERNAL_API_TOKEN>

{
  "stock_id": 1,
  "priority": "batch",
  "task_context": {
    "stock_code": "005930",
    "bgn_de": "20260601",
    "end_de": "20260612"
  }
}
```

### 수집 작업 실행

```http
POST /internal/tasks/collect_dart/run
```

pending 또는 retrying 상태의 `collect_dart` 작업 1건을 claim 후 실행한다.

### 정규화/분석 작업 실행

```http
POST /internal/queue/run-cycle
Content-Type: application/json
X-Internal-Token: <INTERNAL_API_TOKEN>

{"max_passes":10000}
```

### 개발용 E2E 실행

```http
POST /internal/dart/e2e/run
Content-Type: application/json
X-Internal-Token: <INTERNAL_API_TOKEN>

{
  "stock_id": 1,
  "stock_code": "005930",
  "bgn_de": "2026-06-01",
  "end_de": "2026-06-12",
  "force_reprocess": false,
  "priority": "batch",
  "max_normalize_runs": 20,
  "max_analyze_runs": 20,
  "run_until_idle": false
}
```

### 분석 결과 조회

```http
GET /internal/dart/analysis-results?stock_code=005930&limit=20
GET /internal/dart/document-results?stock_code=005930&limit=20
```

`document-results`는 분석 결과를 공시 문서 단위로 평탄화해 보여주므로 개발 확인용으로 적합하다.

### 개발 테스트 데이터 삭제

```http
DELETE /internal/dart/test-data?stock_code=005930&bgn_de=2026-06-01&end_de=2026-06-12
```

해당 기간의 DART 테스트 데이터를 분석 결과부터 원문까지 역순으로 삭제한다.

---

## 9. 실행 예시

### PowerShell

```powershell
# corp code 동기화
$headers = @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/dart/corp-codes/sync" -Headers $headers

# collect_dart 작업 등록
$body = @{
  stock_id = 1
  priority = "batch"
  task_context = @{
    stock_code = "005930"
    bgn_de = "20260601"
    end_de = "20260612"
  }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/tasks/collect_dart/enqueue" -Headers $headers -ContentType "application/json" -Body $body

# 수집, 정규화, 분석 실행
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/tasks/collect_dart/run" -Headers $headers
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/queue/run-cycle" -Headers $headers -ContentType "application/json" -Body '{"max_passes":10000}'

# 결과 조회
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/dart/document-results?stock_code=005930&limit=20" -Headers $headers
```

### curl

```bash
INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:?required}"
curl -X POST http://localhost:8011/internal/dart/corp-codes/sync \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN"
curl -X POST http://localhost:8011/internal/tasks/collect_dart/run \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN"
curl -X POST http://localhost:8011/internal/queue/run-cycle \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"max_passes":10000}'
curl "http://localhost:8011/internal/dart/analysis-results?stock_code=005930&limit=20" \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN"
```

---

## 10. 테스트 관점

### 단위 테스트

- DART API status별 오류 분류와 retryable 여부
- `/document.xml` ZIP/XML 파싱과 텍스트 추출
- 공시명 분류 룰
- event hash 안정성
- features-only DART analysis의 no_signal 계약, derived_features, risk flag
- DART analysis flow 입력 검증, LLM 근거 추출 옵션, graph 메타데이터
- LLM JSON 파싱, 금지 문구 차단, fallback
- 재무 지표 정규식 추출

### 통합 테스트

- `collect_dart`가 신규 원문만 `normalize_dart`로 enqueue하는지 확인
- `force_reprocess=true`일 때 기존 원문도 다시 enqueue하는지 확인
- `normalize_dart`가 `source_documents`, `signal_events`, `signal_metrics`, `validation_logs`를 생성하는지 확인
- `analyze_dart`가 `analysis_results`, `agent_results`를 생성하는지 확인
- `document-results`가 문서 단위로 분석 결과를 평탄화하는지 확인

### 운영 검증

- `DART_API_KEY` 누락 시 수집 실패가 명확한 오류로 남는지 확인
- `dart_corp_codes`에 ticker 매핑이 없을 때 수집 실패가 명확한지 확인
- OpenDART `013` 데이터 없음 응답이 실패가 아니라 0건 수집으로 처리되는지 확인
- `bgn_de > end_de` 같은 요청 오류가 재시도되지 않는지 확인
- schedule로 등록한 `collect_dart`와 실제 `normalize/analyze` 실행 주기를 분리할 수 있는지 확인

---

## 11. 현재 한계와 후속 작업

현재 구현의 주요 한계는 다음과 같다.

- DART 분석 결과는 `analysis_results`, `agent_results`에 저장되고, 성공 시 `aggregate_signal`이 enqueue되어 근거 커버리지용 `final_signals` 생성으로 이어진다.
- DART는 현재 운영 경로에서 방향성/점수 판정을 내지 않는다. 공시 유형과 ownership 이벤트는 근거 피처로만 보존된다.
- LLM 근거 추출은 선택 기능이며, provider key/model 설정이 없으면 자동으로 features-only 분석만 사용한다.
- `DART_FETCH_DOCUMENTS=false`이면 원문 분석 품질이 낮아지고 공시명 중심 이벤트만 생성된다.
- 정정 공시는 별도 이벤트로 남기지만, 원공시와 정정공시의 의미 차이를 비교하는 분석은 아직 없다.
- 공시별 중요도는 `derived_features`로만 보존되며, 실제 점수화가 필요하면 별도 검증된 집계/모델 경로에서 다뤄야 한다.

후속 구현 우선순위:

1. 정정 공시와 원공시 비교 분석을 추가한다.
2. 공급계약, 자사주, 유상증자, 감사의견 등 고임팩트 유형별 세부 rule을 확장한다.
3. LLM 근거 추출 결과의 `key_facts`를 UI evidence 패널에 연결한다.
4. DART 근거와 Report/PRICE 근거가 같은 `score_breakdown`/SYNTHESIZE 표면에서 잘 구분되도록 UI 문구를 정리한다.
