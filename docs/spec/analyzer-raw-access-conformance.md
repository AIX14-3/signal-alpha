# Analyzer ↔ 정규화 계층 정합 설계 검토 (raw 직접조회 규칙)

> 상태: **설계 검토 (미구현)**. 본 문서는 Alternative Signal harness가
> `database/docs/table_responsibility.md`의 Agent 규칙에 어떻게 정합할지를 정리한다.
> 결론부터: **지금 당장 로더를 바꾸지 않는다(현 동작 유지). 노멀라이저 3종 구축이 선행 조건.**

## 1. 규칙과 현재 위반

`database/docs/table_responsibility.md` / `database/README.md §6`:

> **Agent**: Raw 직접 조회 금지. 정규화된 `signal_events`/`signal_metrics` 기반 분석.
> Normalizer가 `raw_documents + detail → source_documents → signal_events → signal_metrics`를 생성한다.

현재 Alternative harness의 evidence 로더는 이 규칙을 어긴다 — raw detail 테이블을 **직접** 읽는다:

| 로더 | 직접 읽는 raw 테이블 | repo 메서드 |
| --- | --- | --- |
| `HiringEvidenceLoader` | `hiring_raw_details` | `list_hiring_details_by_stock` |
| `PatentEvidenceLoader` | `patent_raw_details` | `list_patent_details_by_stock` |
| `DataLabEvidenceLoader` | `datalab_raw_details` | `list_datalab_details_by_category` |

## 2. 왜 이 규칙이 있나 (목적)

- **수치 고정**: 점수·지표·변화율 등 정량값은 LLM이 만들지 않고 DB 정규화 값만 사용(README §8). `signal_metrics`가 그 단일 출처.
- **계층 분리**: 수집(raw) → 정규화(events/metrics) → 분석(Agent) 책임 분리. Agent가 raw 스키마에 결합되면 수집 포맷 변경이 분석기까지 전파된다.
- **중복/추적**: `event_hash`(unique)로 중복 차단, `source_documents`로 근거 추적. raw 직접조회는 이 보증을 우회한다.
- **일관성**: DART는 이미 이 경로를 따른다(`dart/tasks.py` → source_documents/signal_events/signal_metrics). 나머지 소스만 예외 상태.

## 3. 갭 분석 — 지금 바꾸면 깨지는 이유

| 소스 | 노멀라이저 존재? | `signal_events`/`signal_metrics` 채워짐? | 로더를 정규화로 바꾸면 |
| --- | --- | --- | --- |
| DART | ✅ `dart/tasks.py` (`NORMALIZE_DART`) | ✅ | (해당 없음 — Alternative harness 미사용) |
| HIRING | ❌ 없음 | ❌ | **빈 결과 → 분석기 무력화** |
| PATENT | ❌ (`NORMALIZE_PATENT` enqueue만, 소비 핸들러 없음) | ❌ | **빈 결과 → 분석기 무력화** |
| DATALAB | ❌ (`NORMALIZE_DATALAB` enqueue만, 소비 핸들러 없음) | ❌ | **빈 결과 → 분석기 무력화** |

→ 로더만 정규화 계층으로 돌리는 "1줄 정합"은 불가능하다. 정규화 데이터가 없어 분석기가 빈 테이블을 읽게 된다.

## 4. 정합 아키텍처 (제안)

DART 패턴을 그대로 따른다. 소스별 노멀라이저(`processing_queue` 작업 소비)가 raw detail을 정규화 계층으로 변환한다.

```text
{source}_raw_details
  → source_documents (raw_document_id 1:1, source_type, reliability_level)
  → signal_events    (event_type, signal_direction, impact_level, event_hash)
  → signal_metrics   (metric_name, metric_value, previous_value, change_pct, period_*)
```

소스별 매핑 초안:

- **HIRING** — 공고/추세 1건 → `signal_event(event_type='hiring_posting'|'hiring_trend', direction=모멘텀 부호)`,
  `signal_metrics`: `job_count`, `change_pct`, `relative_strength`(14일 baseline 대비). 직군 분류·sector demand는 분석기 단계 유지(파생 지표).
- **PATENT** — 출원 1건 → `signal_event(event_type='patent_filing', direction=significance 기반, impact=significance bucket)`,
  `signal_metrics`: `significance`, `is_new_category`(0/1). LLM 농축은 정규화 **이전** 단계의 캐시로 유지(현 `patent_raw_details.llm_features`).
- **DATALAB** — 검색 관측 → `signal_event(event_type='search_trend', direction=polarity×변화 부호)`,
  `signal_metrics`: `search_index`, `change_pct`. polarity(demand/risk)로 방향 결정.

그 후 로더는 `signal_events`(+`signal_metrics`)를 stock_id로 조회하도록 교체하고, 기존 `list_*_details_by_*` raw 조회 메서드는 분석 경로에서 제거한다(노멀라이저 전용으로만 잔존).

## 5. 단계적 경로 (권장)

| 단계 | 내용 | 상태 |
| --- | --- | --- |
| Phase 0 | **현행 유지** — 로더가 raw 직접조회(동작 보존). 규칙 위반은 본 문서로 명시·추적. | ✅ 현재 |
| Phase 1 | 소스별 노멀라이저 3종 구축(raw→events/metrics), 테스트, `NORMALIZE_*` 핸들러 등록 | ⬜ 별도 워크스트림 |
| Phase 2 | 로더를 정규화 계층 조회로 교체, raw 조회 메서드를 분석 경로에서 분리 | ⬜ Phase 1 이후 |
| Phase 3 | `check_schema`/CI에 "Agent의 raw 직접조회 금지" 정적 검사 추가 | ⬜ 선택 |

## 6. 결론

- **지금 분석기/로더를 바꾸지 않는다.** 정규화 데이터가 없어 즉시 정합은 분석기를 무력화하고 "동작하는 코드 유지" 원칙과 충돌한다.
- 규칙 정합은 **노멀라이저 구축(Phase 1)** 이 선행 조건인 별도 작업이며, 본 문서가 그 설계와 매핑을 정의한다.
- DART가 이미 동일 경로를 따르므로 아키텍처적 선례는 검증됨 — 신규 작업은 그 패턴 복제.

관련: `database/docs/table_responsibility.md`, `database/README.md §6`, `services/agent-worker/app/orchestrator/dart/tasks.py`(정규화 선례).
