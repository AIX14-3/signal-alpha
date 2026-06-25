# Signal α — 용어집 / 규약

프로젝트 전반에서 일관되게 쓰는 도메인 용어, 점수 개념, 소스/에이전트 명칭, 표현 규칙을 정리합니다.

## 점수·집계 용어

| 용어 | 의미 |
|---|---|
| `consensus_score` | 소스들이 같은 방향을 가리키는 정도를 나타내는 종합 점수 |
| `alignment_rate` | 소스 방향성 일치 수준 (`HIGH` / `MEDIUM_HIGH` / `MEDIUM` / `LOW`) |
| `source_agreement` | 소스별 방향성 맵 (예: `{dart: positive, report: positive, alternative: neutral}`) |
| `overall_direction` | 종합 방향 (`positive` / `neutral` / `negative` / `mixed` / `unknown`) |
| `positive_evidence` | 데이터가 같은 방향을 보이는 팩트 목록 |
| `caution_evidence` | 데이터 충돌·표본 부족·의견 차이 등 주의 근거 |
| `needs_review` | 추가 확인이 필요한 상태 플래그 |

> `confidence`는 사용하지 않습니다 — 투자 조언의 신뢰도처럼 해석될 수 있어, 위 `consensus_score` /
> `alignment_rate` / `source_agreement`로 대체합니다.

## 소스 / 에이전트

| 소스 타입 | 에이전트 | 데이터 |
|---|---|---|
| `dart` | DART Watcher | 공시·분기 실적 (OpenDART) |
| `report` | Report RAG | 증권사 리포트 (메타 + PDF Local RAG) |
| `price` | PRICE Analyzer | 키움 REST 수집 가격(`price_snapshots`, `ohlcv_data`) |
| `alternative` | Alternative Signal | 채용·특허(KIPRIS)·네이버 DataLab·SEC |
| (통합) | Debate Aggregation | 소스 결과 통합 → `final_signals` |

## 공통 데이터 계약 (요약)

```ts
type Direction = "positive" | "neutral" | "negative" | "mixed" | "unknown";
type SourceType = "dart" | "report" | "price" | "alternative";

type SourceResult = {        // 소스 에이전트 1개의 출력
  source: SourceType; agent: string;
  stock_code: string; stock_name: string;
  direction: Direction; score?: number; summary: string;
  evidence_items: { title: string; summary: string; url?: string; published_at?: string }[];
  risk_flags: string[];
  data_status?: "ok" | "partial" | "failed";   // 수집 실패 시 partial/failed
};

type AggregatedSignal = {    // 집계 결과 → final_signals
  stock_code: string; stock_name: string;
  consensus_score: number;
  alignment_rate: "HIGH" | "MEDIUM_HIGH" | "MEDIUM" | "LOW";
  overall_direction: Direction;
  source_agreement: Record<SourceType, Direction>;
  positive_evidence: string[]; caution_evidence: string[];
  needs_review: boolean; summary: string;
};
```

> 정확한 필드는 `packages/signal-core`의 스키마와 `spec/source-agent-contract.md`,
> `spec/final-signal-aggregator-spec.md`를 기준으로 합니다.

## 데이터 레이어 (L0–L10)

원천 문서(L0/raw)에서 정규화·이벤트·지표를 거쳐 분석/집계로 올라가는 계층 모델입니다.
정의는 `spec/data-foundations-and-l1-l10-workflow.md`, `spec/data-layers-l2-l10-spec.md` 참고.

## 작업(큐) 타입

`processing_queue` 기반으로 단계가 분리됩니다. DART 예: `collect_dart` → `normalize_dart` → `analyze_dart`.
흐름은 [data-pipeline.md](./data-pipeline.md) 참고.

## 표현 규칙 (제품 문구)

- **금지**: 매수/매도/보유 추천, "지금 사야 한다", 상승 보장·목표 수익률·수익 예측, 추천 종목,
  투자 타이밍 알림, 매집 구간, 단기 급등 가능성
- **권장**: 데이터 방향성, 소스 간 일치도, 근거, 데이터 정합성, 추가 확인 필요, 사용자 판단 보조

전체 가드레일과 배경은 [overview.md](./overview.md) 및 루트 `AGENTS.md` 참고.

## 기타 약어

- **Signal Journal**: 사용자의 주관적 판단 기록·복기 도구 (플랫폼이 성과 평가/추천하지 않음)
- **RAG**: 리포트 청크를 pgvector로 검색해 LLM 분석에 근거를 제공하는 방식
- **corp_code**: DART 고유 기업 코드 (종목코드와 별도 매핑 필요)
