# Source Agent 공통 계약 스펙

> 기준일: 2026-06-16
> 대상: `services/agent-worker/app/agents`
> 목적: DART, Report, PRICE, Alternative 분석 Agent를 같은 입출력 계약으로 맞추고, 이후 LangGraph node로 연결하기 위한 공통 기준을 정의한다.

---

## 1. 배경

기존 `Analyzer` 계약은 `RawEvidence`를 받아 `SourceResult`를 반환하는 수집-분석 파이프라인용 인터페이스다. 반면 DART 분석은 이미 `signal_events`와 `analysis_results`/`agent_results` 저장 흐름을 사용하므로, LangGraph 도입 시에는 더 상위 레벨의 Agent 계약이 필요하다.

이번 계약은 기존 `SourceResult`를 제거하지 않는다. `SourceResult`는 기존 collector/analyzer pipeline 호환을 위해 유지하고, `SourceAgentInput`/`SourceAgentOutput`은 저장된 정규화 데이터와 Agent 실행 결과를 다루는 상위 계약으로 사용한다.

---

## 2. 책임 경계

| 계층 | 책임 | 하지 않는 일 |
|---|---|---|
| Collector | 외부 API/DB에서 원천 데이터를 수집하고 `RawEvidence` 또는 raw table에 저장 | 방향성, 점수, LLM 판단 |
| Normalizer | raw 문서를 `source_documents`, `signal_events`, `signal_metrics`로 변환 | 최종 분석 결과 저장 |
| Source Agent | 정규화 이벤트/evidence를 읽어 source별 방향성, 점수, 리스크, method detail 생성 | 외부 수집 API 호출 |
| Orchestrator/TaskHandler | 큐 실행, DB 조회, Agent 호출, `analysis_results`/`agent_results` 저장 | source별 판단 로직 직접 보유 |
| LangGraph workflow | Source Agent 실행 순서, 조건부 실행, 검증/fallback 흐름 관리 | 개별 source 분석 룰 직접 구현 |

---

## 3. 공통 타입

구현 위치: `services/agent-worker/app/agents/base.py`

### `SourceAgentInput`

| 필드 | 타입 | 설명 |
|---|---|---|
| `source` | `SourceType` | 실행 source. 예: `DART`, `REPORT`, `PRICE` |
| `stock_code` | `str` | 종목 코드 |
| `stock_id` | `int \| None` | DB stock id. 없으면 `None` |
| `analysis_date` | `date \| None` | 분석 기준일 |
| `run_key` | `str \| None` | 재실행/중복 제어용 실행 키 |
| `events` | `list[dict[str, Any]]` | 정규화된 `signal_events` 기반 입력 |
| `evidence` | `list[RawEvidence]` | 기존 analyzer 호환 또는 raw evidence 기반 입력 |
| `context` | `dict[str, Any]` | source별 추가 실행 컨텍스트 |

### `SourceAgentOutput`

| 필드 | 타입 | 설명 |
|---|---|---|
| `source` | `SourceType` | 결과 source |
| `stock_code` | `str` | 종목 코드 |
| `direction` | `Direction` | `positive`, `negative`, `neutral`, `mixed`, `unknown` |
| `score` | `float` | source별 signed score. 원칙적으로 -1.0~1.0 범위를 사용하고, 저장 계층에서 필요한 범위로 매핑 |
| `summary` | `str` | source 분석 요약 |
| `risk_flags` | `list[str]` | 리스크/주의 플래그 |
| `method_detail` | `dict[str, Any]` | `agent_results.method_detail`에 저장할 상세 JSON |
| `needs_review` | `bool` | 사람 검토 또는 추가 확인 필요 여부 |
| `data_status` | `ok \| partial \| failed` | 데이터 완전성 상태 |
| `analysis_source` | `str` | `rules`, `llm`, `rules_fallback` 등 분석 출처 |
| `llm_model` | `str \| None` | LLM 사용 시 모델명 |
| `prompt_ver` | `str` | rule 또는 prompt 버전 |
| `llm_error` | `str \| None` | LLM fallback 시 오류 메시지 |

### `SourceAnalysisAgent`

```python
class SourceAnalysisAgent(Protocol):
    source: SourceType

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        ...
```

---

## 4. DART 적용 방식

`DartAnalysisGraphAgent`는 `SourceAnalysisAgent` 계약을 따르며, 내부 node에서 `DartAnalysisAgent`를 호출한다.

```python
result = await DartAnalysisGraphAgent(...).analyze(
    SourceAgentInput(
        source="DART",
        stock_code="005930",
        stock_id=1,
        analysis_date=analysis_date,
        run_key="DART_EVENT_501",
        events=events,
        context={"source_type": "DART"},
    )
)
```

DART analysis flow:

```text
validate_input
  -> analyze
  -> validate_output
```

각 node 책임:

- `validate_input`: source, stock code, event 입력을 검증하고 실패 시 `data_status="failed"` 결과를 만든다.
- `analyze`: `DartAnalysisAgent`를 호출해 features-only 분석과 선택적 LLM 근거 추출을 수행한다.
- `validate_output`: `method_detail.graph`, `method_detail.graph_nodes`를 추가해 실행 경로를 남긴다.

`DartAnalysisAgent` 내부 책임:

- `build_dart_analysis_result(events)`로 rule 기반 결과 생성
- `DartLlmEvidenceExtractor`가 주입된 경우에만 LLM 근거 추출 시도
- LLM 성공 시 `method_detail.llm_evidence`에 `summary`, `key_facts`, `risk_flags`, `confidence`를 additive로 포함
- LLM 실패 시 features-only 결과를 유지하고 `analysis_source="features"`, `llm_error` provenance 반환

`DartAnalyzeTaskHandler`는 `SourceAgentInput`을 만들고 기본값으로 `DartAnalysisGraphAgent`를 호출한 뒤 기존처럼 `analysis_results`, `agent_results`에 저장한다.

---

## 5. Report/PRICE/LangGraph 확장 기준

Report와 PRICE도 같은 형태로 맞춘다.

```text
ReportAnalysisAgent.analyze(SourceAgentInput) -> SourceAgentOutput
PriceAnalysisAgent.analyze(SourceAgentInput) -> SourceAgentOutput
```

DART는 `DartAnalysisGraphAgent`라는 호환 이름을 유지하지만 현재 LangGraph 런타임 의존성 없이 입력 검증 → Agent 호출 → 출력 메타데이터 보강 흐름을 직접 수행한다. Report/PRICE 확장 시에도 각 단계는 `SourceAgentInput`을 구성해 Agent를 호출하고, `SourceAgentOutput`을 state에 누적한다.

```text
load_context
  -> run_dart_agent
  -> run_report_agent
  -> run_price_agent
  -> aggregate
  -> persist_final_signal
```

이때 LangGraph는 실행 순서, 조건부 실행, partial failure 처리를 담당하고, 개별 source 판단 로직은 각 Source Agent 내부에 둔다.

---

## 6. 비범위

- DB 마이그레이션 없음
- 외부 API 경로 변경 없음
- Report/PRICE Agent 전환은 후속 작업
- 다중 source LangGraph workflow 구현은 후속 작업
- `SourceResult` 제거 또는 대체 없음
