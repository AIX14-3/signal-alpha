# Final Signal Aggregator Spec

> Status: MVP task implemented
> Created: 2026-06-19  
> Target: `services/agent-worker/app/orchestrator`  
> Related docs:
> - `docs/archive/planning/06_통합단계_Aggregator.md`
> - `docs/archive/planning/09_DB설계.md`
> - `docs/spec/dart-collector-analyzer-spec.md`
> - `docs/spec/source-agent-contract.md`
> - `docs/spec/hiring-cutover-and-final-signals-naming.md`

---

## 1. Purpose

Final Signal Aggregator는 source별 분석 결과를 사용자-facing 최종 표시 단위인
`final_signals`로 만드는 agent-worker 내부 오케스트레이션 계층이다.

목표 흐름:

```text
Collector
-> Normalizer
-> Source Agent
-> analysis_results / agent_results
-> Final Signal Aggregator
-> final_signals
-> Main Server API
-> Web
```

이 스펙은 MVP Aggregator 계약과 현재 구현 기준을 정의한다.

---

## 2. Current State

현재 문서와 구현 기준 상태는 다음과 같다.

- DART는 `collect_dart`, `normalize_dart`, `analyze_dart`가 구현되어 있다.
- DART 분석 결과는 `analysis_results`, `agent_results`에 저장된다.
- DART 분석 성공 시 `aggregate_signal`이 enqueue되고, Aggregator가 DART 단일 source 기반
  `final_signals`를 생성한다.
- Main Server 대시보드는 `final_signals`를 기준으로 최신 시그널을 조회한다.
- `final_signals`를 실제로 쓰는 기존 구현은 Alternative 계열 persistence가 있다.
- 전체 DART/PRICE/REPORT/ALTERNATIVE 통합 Aggregator는 아직 구현되어 있지 않다.

기존 `AlternativeSignalPersistence`는 현재 동작을 유지한다. 다만 신규 전체 통합 경로에서는
source analyzer가 `final_signals`를 직접 쓰지 않고 Aggregator를 통해 쓰는 것을 원칙으로 한다.

---

## 3. Scope

### In Scope

- `analysis_results`와 `agent_results`를 읽어 최종 표시용 `final_signals`를 생성한다.
- source별 signed score를 공통 범위 `-1.0 ~ 1.0`으로 정규화한다.
- `final_score`, `signal`, `source_agreement`, `consensus_score`, `warning_level`, `needs_review`를 결정한다.
- `score_breakdown` JSON 구조를 고정한다.
- DART 단일 source만 있어도 Aggregator를 통해 MVP final signal을 만들 수 있게 한다.
- 향후 PRICE, REPORT, ALTERNATIVE 결과가 같은 계약으로 합류할 수 있게 한다.

### Out of Scope

- LLM debate, Bull/Bear/Judge 다중 에이전트 구현
- ML score 보정
- 백테스팅 기반 가중치 튜닝
- DB 테이블 리네임
- `final_signals` 스키마 변경
- Main Server API 신규 구현

---

## 4. Core Decision

MVP에서는 `final_signals`를 **사용자-facing 최종 표시 결과**로 유지한다.

따라서 신규 통합 흐름에서는 다음 규칙을 따른다.

```text
Source Agent는 analysis_results / agent_results까지만 저장한다.
Final Signal Aggregator가 agent_results를 읽어 final_signals를 생성한다.
Main Server와 Web은 final_signals를 중심으로 조회한다.
```

파이프라인별 발행 결과를 모두 `final_signals`에 직접 적재하는 모델은 채택하지 않는다.
필요하면 나중에 `published_signals` 또는 `signal_outputs` 같은 별도 이름을 검토한다.

---

## 5. Aggregator Task

### Proposed Task Type

```text
aggregate_signal
```

`processing_queue.task_type`은 `VARCHAR(50)`이며 enum CHECK가 없으므로 새 task type 추가에
DB 마이그레이션은 필요하지 않다. 코드는 `services/agent-worker/app/orchestrator/queue/task_types.py`
상수와 handler 등록을 포함한다.

기존 기획 문서의 `AGGREGATE_DEBATE`, `CREATE_FINAL_SIGNAL`은 더 큰 통합/토론 단계 이름이다.
MVP에서는 둘을 나누지 않고 `aggregate_signal` 하나가 final signal 생성까지 담당한다.

### Input Sources

Aggregator task는 둘 중 하나를 입력으로 받는다.

```json
{
  "source_analysis_result_ids": [101, 102],
  "task_context": {
    "stock_code": "005930",
    "signal_date": "2026-06-19",
    "run_key": "AGGREGATED"
  }
}
```

또는 source result id가 없으면 stock/date/run_key 기준으로 최신 source 결과를 조회한다.

```json
{
  "stock_id": 1,
  "task_context": {
    "stock_code": "005930",
    "signal_date": "2026-06-19",
    "run_key": "AGGREGATED"
  }
}
```

MVP 구현에서는 명시적인 `source_analysis_result_ids` 입력을 우선 지원한다.
자동 최신 조회는 후속 확장으로 둘 수 있다.

---

## 6. Source Result Normalization

Aggregator는 source별 `agent_results`를 다음 내부 형태로 정규화한다.

```python
NormalizedSourceResult = {
    "source": "DART",
    "analysis_result_id": 101,
    "agent_result_id": 201,
    "direction": "positive",
    "score": 0.42,
    "score_100": 71.0,
    "data_status": "ok",
    "needs_review": False,
    "risk_flags": [],
    "summary": "DART 공시 기준 데이터 방향성은 긍정입니다.",
    "evidence_refs": [501, 502],
}
```

### Source Identification

Source Agent가 저장하는 `agent_results.method_detail.source`는 필수 계약이다.
Aggregator 구현 전에 각 source handler 테스트에서 이 값이 저장되는지 검증해야 한다.

DART MVP 기준 현재 경로는 `build_dart_analysis_result()`가 `method_detail.source="DART"`를
생성하고, `DartAnalyzeTaskHandler`가 이를 `agent_results.method_detail`에 그대로 저장한다.

source는 아래 순서로 결정한다.

1. `agent_results.method_detail.source`
2. `agent_results.method_detail.source_type`
3. `analysis_results.run_key`
4. `analysis_results.analysis_mode`
5. 식별 실패 시 `UNKNOWN` 처리 후 해당 source result는 aggregation에서 제외하고 `needs_review=true`

`UNKNOWN`으로 빠진 result가 있으면 Aggregator는 `risk_flags`에
`unknown_source_result`를 추가하고 validation log를 남긴다.

### Score Extraction

source signed score는 아래 순서로 읽는다.

1. `agent_results.method_detail.source_score`
2. `agent_results.method_detail.score`
3. `agent_results.method_score`를 역변환

역변환 공식:

```text
signed_score = (method_score / 50) - 1
```

저장용 0~100 변환 공식:

```text
score_100 = (signed_score + 1) * 50
```

모든 signed score는 `-1.0 ~ 1.0` 범위로 clamp한다.
모든 DB 저장용 score는 `0 ~ 100` 범위로 clamp한다.

---

## 7. Aggregation Rules

### Available Sources

Canonical source set:

```text
DART
PRICE
REPORT
ALTERNATIVE
```

MVP에서는 하나 이상의 source result가 있으면 final signal을 생성할 수 있다.
없는 source는 `score_breakdown`에서 `data_status="missing"`으로 표현한다.

PRICE는 canonical source set에는 포함하지만, multi-source scoring에 합류하기 전까지는
검증/오버레이 source로 취급한다. PRICE를 `aggregate_score`에 넣을지 여부는
`feat/final-signal-multi-source-aggregation` 전에 결정해야 한다. MVP DART-only Aggregator는
DART source만 scoring source로 사용한다.

### Score

MVP 기본 점수는 available scoring source의 signed score 평균이다.

```text
aggregate_score = average(available_scoring_source_scores)
final_score = (aggregate_score + 1) * 50
```

후속 단계에서 source별 가중치를 도입할 수 있다. 가중치를 도입할 때도 missing source는
분모에서 제외하고, 실제 available scoring source의 가중치만 재정규화한다.

### Direction

최종 `signal`은 signed score와 source 충돌 여부로 결정한다.
방향 충돌 판단은 점수 임계값 판단보다 항상 먼저 실행한다.
아래 순서는 normative rule이며 구현 시 재정렬하지 않는다.

```text
has_mixed_source = any(source.direction == "mixed")
has_positive = any(source.direction == "positive")
has_negative = any(source.direction == "negative")

if 모든 available source가 neutral이면:
    signal = "neutral"
elif has_mixed_source:
    signal = "mixed"
elif has_positive and has_negative:
    signal = "mixed"
elif aggregate_score >= 0.2:
    signal = "positive"
elif aggregate_score <= -0.2:
    signal = "negative"
else:
    signal = "neutral"
```

이 우선순위는 구현 시 반드시 유지한다. 예를 들어 DART가 `positive`, score `1.0`이고
PRICE가 `negative`, score `-0.5`이면 평균 score는 `0.25`다. 점수만 보면 positive
임계값을 넘지만, positive와 negative source가 공존하므로 최종 `signal`은 `mixed`다.

권장 테스트명:

```text
positive_and_negative_sources_resolve_to_mixed_before_score_threshold
```

### Source Agreement

`source_agreement`는 available source 간 방향 일치도를 나타낸다.
사용자-facing API에서는 `alignment_rate`로 표현할 수 있다.

```text
agreement_rate = dominant_direction_count / available_source_count
```

매핑:

```text
HIGH   = agreement_rate >= 0.75
MEDIUM = agreement_rate >= 0.5
LOW    = agreement_rate < 0.5
```

available source가 1개뿐이면 `source_agreement="LOW"`로 둔다.
이는 단일 source 결과가 "강한 합의"처럼 보이는 것을 막기 위한 정책이다.

### Consensus Score

`consensus_score`는 투자 확신도가 아니라 소스 간 일치도/정합성 점수다.

```text
consensus_score = round(agreement_rate * 100, 2)
```

available source가 1개뿐이면 `consensus_score=50.0`으로 둔다.
source가 하나뿐인 상태를 과도한 합의로 표현하지 않기 위함이다.

### Warning Level

```text
WARNING:
  - available source가 0개
  - available scoring source가 0개
  - failed source가 2개 이상이고 최종 결과를 설명할 available source가 부족함
  - Aggregator가 stable aggregate parent row를 만들 수 없음

CAUTION:
  - available source가 1개
  - missing source가 2개 이상
  - positive와 negative source가 공존함
  - source result 중 direction="mixed"가 있음
  - needs_review=true source가 1개 이상
  - data_status="partial" source가 1개 이상

NORMAL:
  - 위 조건에 해당하지 않음
```

소스 간 방향 충돌은 데이터 실패가 아니다. 충돌 또는 mixed 상태는 사용자에게 유용한
"소스 분열" 정보이므로 `CAUTION` + `needs_review=true`로 공개한다. 숨기는 대상은
데이터 실패, 분석 불가, parent row 생성 실패처럼 최종 결과를 신뢰 가능한 형태로 만들 수
없는 경우로 제한한다.

### Needs Review

`needs_review=true` 조건:

- 최종 `signal="mixed"`
- `warning_level`이 `CAUTION` 또는 `WARNING`
- source result 중 하나라도 `needs_review=true`
- source result 중 하나라도 `data_status="failed"`
- LLM fallback 또는 LLM error가 method detail에 존재

---

## 8. final_signals Mapping

Aggregator는 `AnalysisRepository.upsert_final_signal()`을 통해 저장한다.

| final_signals column | Mapping |
|---|---|
| `stock_id` | task stock_id |
| `analysis_result_id` | aggregate 대표 `analysis_results.id` |
| `signal_date` | task context signal_date 또는 source analysis_date 최신값 |
| `run_key` | 기본 `AGGREGATED` |
| `version` | aggregator version, 예: `final-agg-v1` |
| `final_score` | aggregate signed score를 0~100으로 변환 |
| `confidence` | 내부 호환 컬럼. `consensus_score`와 같은 값 저장 |
| `signal` | 최종 direction |
| `source_agreement` | HIGH / MEDIUM / LOW |
| `warning_level` | NORMAL / CAUTION / WARNING |
| `score_breakdown` | source별 상세 JSON |
| `summary` | rule 기반 요약 |
| `bull_point` | positive evidence 요약 |
| `bear_point` | caution evidence 요약 |
| `disclaimer` | 기본 disclaimer 사용 |
| `needs_review` | Aggregation rule 결과 |
| `min_plan_required` | 기본 `free` |
| `is_published` | MVP 기본 `true`, 단 WARNING이면 `false` |
| `published_at` | publish 시 현재 시각 또는 signal_date |
| `consensus_score` | 소스 간 일치도/정합성 점수 |
| `positive_evidence` | positive evidence JSON |
| `caution_evidence` | caution evidence JSON |

`analysis_result_id`는 Aggregator 실행 자체의 대표 row를 참조해야 한다.
MVP에서는 aggregate task가 새 `analysis_results` row를 `analysis_mode="full"`,
`run_key="AGGREGATED"`, `version="final-agg-v1"`로 생성한다.

`confidence`는 DB 내부 호환 컬럼명이다. 사용자-facing API, Web, 발표자료에서는
`confidence` 또는 "신뢰도"라는 라벨을 사용하지 않는다. `final_score`는
"데이터 방향성 점수", `consensus_score`/`alignment_rate`는 "소스 간 일치도"로 매핑한다.

---

## 9. score_breakdown Contract

`score_breakdown`은 source별 object를 가진다.
스칼라 숫자만 저장하는 형태는 신규 Aggregator 경로에서 사용하지 않는다.

```json
{
  "DART": {
    "direction": "neutral",
    "score": 0.0,
    "score_100": 50.0,
    "data_status": "ok",
    "needs_review": false,
    "analysis_result_id": 101,
    "agent_result_id": 201,
    "risk_flags": []
  },
  "PRICE": {
    "direction": "unknown",
    "score": null,
    "score_100": null,
    "data_status": "missing",
    "needs_review": true,
    "analysis_result_id": null,
    "agent_result_id": null,
    "risk_flags": ["missing_source"]
  },
  "REPORT": {
    "direction": "unknown",
    "score": null,
    "score_100": null,
    "data_status": "missing",
    "needs_review": true,
    "analysis_result_id": null,
    "agent_result_id": null,
    "risk_flags": ["missing_source"]
  },
  "ALTERNATIVE": {
    "direction": "unknown",
    "score": null,
    "score_100": null,
    "data_status": "missing",
    "needs_review": true,
    "analysis_result_id": null,
    "agent_result_id": null,
    "risk_flags": ["missing_source"]
  }
}
```

Main Server는 `score_breakdown[source].score`를 source signed score로 사용할 수 있다.
화면에서 0~100 표시가 필요하면 `score_100` 또는 `final_score`를 사용한다.

---

## 10. Summary and Evidence

MVP summary는 rule 기반으로 생성한다. LLM summary는 후속 기능이다.

금지:

- 매수, 매도, 보유 추천
- 지금 사거나 팔아야 한다는 표현
- 목표 수익률, 상승 보장, 수익 예측
- 추천 종목, 투자 타이밍 알림

허용 표현:

- 데이터 방향성
- 근거
- 소스 간 일치도
- 데이터 정합성
- 추가 확인 필요
- 사용자 판단 보조

예시:

```text
DART 기준 데이터 방향성은 중립이며, PRICE와 REPORT 데이터는 아직 연결되지 않았습니다.
현재 결과는 단일 소스 기반이므로 추가 확인이 필요합니다.
```

`positive_evidence`와 `caution_evidence`는 source별 summary, key_facts, event titles,
source_signal_event_ids를 바탕으로 구성한다.

---

## 11. Publishing Policy

MVP publishing rule:

```text
if warning_level == "WARNING":
    is_published = false
else:
    is_published = true
```

단일 source만 있는 결과도 `CAUTION` + `needs_review=true`로 published 가능하다.
이는 대시보드에서 "데이터 없음"이 아니라 "단일 source 기반 데이터 방향성"을 확인하기 위한 MVP 정책이다.

source disagreement도 published 가능하다. `signal="mixed"` 또는 positive/negative source가
공존하는 결과는 `CAUTION` + `needs_review=true` + `is_published=true`로 저장해
사용자가 소스 간 불일치를 확인할 수 있게 한다.

`WARNING` + `is_published=false`는 데이터 실패나 분석 불가에 한정한다.

운영 단계에서는 다음 중 하나로 강화할 수 있다.

- 최소 2개 source 이상일 때만 publish
- DART/PRICE 중 하나 이상 필수
- LLM fallback 결과는 publish하지 않고 review queue로 보냄
- `is_published=false` draft를 Main Server에서 별도 표시

---

## 12. Queue Flow

MVP DART 기준 흐름:

```text
collect_dart
-> normalize_dart
-> analyze_dart
-> aggregate_signal
-> final_signals
```

현재 MVP 구현:

```text
analyze_dart handler가 성공하면 aggregate_signal을 enqueue한다.
```

이유:

- DART 단일 source MVP를 바로 화면에서 검증할 수 있다.
- source_analysis_result_ids를 명시적으로 넘길 수 있어 조회 범위가 작다.
- 이후 Report/PRICE가 붙으면 stock/date 기준 재집계 스케줄러로 확장할 수 있다.

### Idempotency and Concurrent Aggregate Tasks

후속 단계에서 `analyze_dart`, `analyze_price`, `analyze_report`가 거의 동시에 끝나면
`aggregate_signal` task가 여러 개 enqueue될 수 있다. Aggregator는 이 상황에서도 같은
stock/date/version의 최종 결과가 하나의 aggregate parent row와 하나의 final signal row로
수렴하도록 멱등성을 가져야 한다.

Aggregator는 source별 run key를 aggregate parent row의 run key로 사용하지 않는다.
항상 stable aggregate identity를 먼저 계산한다.

```text
aggregate_identity =
  stock_id
  + signal_date
  + analysis_mode="full"
  + run_key="AGGREGATED"
  + version="final-agg-v1"
```

이 identity는 `analysis_results`의 unique key와 일치해야 한다.

```text
stock_id + analysis_date + analysis_mode + run_key + version
```

Handler는 이 identity로 `analysis_results`를 upsert하고, 반환된 parent
`analysis_result_id`를 `final_signals.analysis_result_id`로 사용한다.
동시에 여러 aggregate task가 실행되어도 unique constraint와 upsert를 통해 같은 parent row를
갱신해야 한다.

`final_signals`도 같은 stable identity를 사용한다.

```text
stock_id + signal_date + run_key="AGGREGATED" + version="final-agg-v1"
```

구현 가이드:

- `aggregate_signal` enqueue 시 가능하면 `dedupe=true`를 사용한다.
- `task_context.aggregation_key`를 둘 수 있다.
- handler는 task 입력만 신뢰하지 말고 실행 시점의 최신 available source results를 다시 조회할 수 있어야 한다.
- 여러 source analyzer가 같은 날 순차/동시 완료되어도 Aggregator는 insert 증식이 아니라 같은 aggregate row 갱신으로 동작해야 한다.
- 후속 multi-source 단계에서는 source별 완료 이벤트마다 aggregate를 재실행하되, 결과는 같은 `AGGREGATED` identity에 수렴시킨다.

---

## 13. API Impact

Main Server는 신규 Aggregator를 직접 호출하지 않는다.

Main Server 영향:

- `GET /api/dashboard`는 이미 `final_signals`를 읽는다.
- Aggregator가 `final_signals`를 생성하면 대시보드에 최신 데이터 방향성이 표시된다.
- 이후 `GET /api/signals/{signal_id}` 상세 API는 `final_signals`, `agent_results`,
  `signal_events`, `source_documents`를 조합해 근거를 보여줄 수 있다.

Web 영향:

- `latest_signal.score`는 `final_signals.final_score` 기반 0~100 표시값이며, 화면 라벨은
  "데이터 방향성 점수"를 사용한다.
- `source_summary[].score`는 `score_breakdown[source].score` 기반 signed source score다.
- `alignment_rate` 또는 `consensus_score`는 "소스 간 일치도"로 표시한다.
- DB 내부 `confidence` 컬럼은 사용자-facing 응답과 화면 라벨에 노출하지 않는다.
- 필요한 경우 `source_summary[].score_100` 노출을 Main Server에서 추가할 수 있다.
- Forecast/Kronos 계열 예측 정보는 `score_breakdown`에 넣지 않는다. 가격 예측은 매매 신호가
  아니라 교차검증 재료이므로 별도 `forecast_overlay` 또는 `validation_overlay` 계약으로
  내려준다.

---

## 14. Validation and Tests

구현 시 최소 테스트:

- source score 추출 우선순위
  - `method_detail.source_score`
  - `method_detail.score`
  - `method_score` 역변환
- source agent가 `agent_results.method_detail.source`를 저장하는지 검증
- source 식별 실패 시 `UNKNOWN` 결과를 제외하고 `unknown_source_result` risk flag를 남김
- signed score clamp
- 0~100 저장 score 변환
- DART 단일 source aggregation
- missing source가 `score_breakdown`에 포함되는지
- 단일 source일 때 `source_agreement=LOW`, `consensus_score=50.0`
- positive/negative 충돌 시 점수 임계값보다 먼저 `signal=mixed`, `needs_review=true`
- 하위 source 중 `direction=mixed`가 있으면 점수 임계값보다 먼저 최종 `signal=mixed`
- source disagreement는 `CAUTION` + `is_published=true`
- 동시에 여러 `aggregate_signal` task가 실행되어도 같은 stable aggregate parent row를 upsert
- source별 run key가 aggregate parent row의 run key로 섞이지 않는지 검증
- 데이터 실패/분석 불가로 인한 WARNING이면 `is_published=false`
- `final_signals.score_breakdown`이 object 구조인지
- 금지된 투자 추천 문구가 summary에 없는지
- `aggregate_signal` queue handler 등록

권장 실행:

```powershell
cd services/agent-worker
uv run pytest tests/test_final_signal_aggregator.py -q
uv run pytest tests -q
uv run ruff check app/orchestrator tests/test_final_signal_aggregator.py
git diff --check
```

---

## 15. Open Decisions

아래는 MVP 구현 전에 확정하거나, MVP 이후 별도 PR로 넘겨도 되는 결정사항이다.

| Decision | MVP Default | Later Option |
|---|---|---|
| source weighting | available scoring source 단순 평균 | source별 configurable weight |
| PRICE scoring role | DART-only MVP에서는 제외. multi-source 전 결정 필요 | validation overlay only 또는 weighted scoring source |
| publish minimum source count | 1개 source도 CAUTION으로 publish | 최소 2개 source 요구 |
| run_key | `AGGREGATED` | 전략별 `AGGREGATED_D1`, `AGGREGATED_LLM` |
| LLM summary | 사용 안 함 | 검증된 LLM summary 추가 |
| Debate D-2~D-5 | 사용 안 함 | 백테스팅 후 비교 |
| Alternative direct final_signals write | 기존 동작 유지. multi-source 전 충돌 정리 필요 | direct write off 또는 `run_key=ALTERNATIVE` 분리 후 Aggregator 경로로 통합 |
| `final_signals` naming | 유지 | `published_signals` 리네임 검토 |
| Forecast/Kronos | score_breakdown 제외 | `forecast_overlay` / `validation_overlay` API 계약 |
| User-facing score labels | `final_score`는 데이터 방향성 점수, `consensus_score`는 소스 간 일치도 | Web/Main Server 문서와 UI 문구 동기화 |

---

## 16. Implementation Backlog

추천 feature sequence:

1. `feat/final-signal-aggregator-spec`
   - 이 문서 추가
2. `feat/final-signal-aggregator-task` - 구현됨
   - `aggregate_signal` task handler 구현
   - DART 단일 source aggregation 지원
3. `feat/main-server-signal-detail-api`
   - `final_signals` 상세 근거 조회 API
4. `feat/final-signal-multi-source-aggregation`
   - Report/PRICE/ALTERNATIVE source 결과 합류
5. `feat/final-signal-aggregator-backtest`
   - 가중치와 publish rule 검증
