# 05. 분석 단계 (Analyzer) — 상세 스펙

> Signal α 하위 문서 | 상위: [00_메인_기획서.md](00_메인_기획서.md)

---

## 5.1 핵심 컨벤션

> **규칙 1.** 수치는 LLM이 생성하지 않는다. DB에서 꺼내 템플릿에 삽입하고, LLM은 문장만 생성한다.
> **규칙 2.** 모든 LLM 출력은 검증 함수를 통과한 후에만 프론트엔드로 전달된다. (`packages/signal-core/signal_core/safety.py`)
> **규칙 3.** 분석기는 외부 API를 직접 호출하지 않고 DB만 읽는다. (정규화 테이블 기준 — `source_documents`·`signal_events`·`signal_metrics`, RAG 복구 시 `report_chunks`)

---

## 5.2 공식 데이터 분석기

| 항목 | A-1 Dart_Analyzer (구현) | A-2 Report_Analyzer (구현) |
| --- | --- | --- |
| 역할 | 공시 임팩트 분석 | 리포트 수집/정규화와 밸류에이션 가정 구조화 |
| 입력 | DART 정규화 데이터 (dart_raw_details 경유) | report_raw_details · report_valuation_facts · source_documents/signal_events |
| 처리 | 고임팩트 즉시 / 저임팩트 배치 | 배치 전용 |
| 핵심 로직 | ① Few-shot 방향 일관성 ② 정정공시 반전 ③ BEAT/MISS 분류 (`app/analyzers/dart/` — financials·rules·llm) | ① PDF 저장/파싱 ② 목표가·의견 원천값 추출 ③ EPS/적용 배수/피어 그룹 구조화 ④ 내재 배수(`target_price / forward_eps_est`) 결정론 계산 ⑤ Gemini 기반 카테고리 변화와 재평가 thesis 패러프레이즈 ⑥ 배수 분산과 scenario band helper |
| 출력 | score, signal, top_disclosures, earnings_surprise, summary | report_valuation_facts, source_documents, signal_events, signal_metrics, scenario_band helper |
| 사용 모델 | Gemini 3 Flash (프로바이더·모델은 env 주입: `DART_LLM_PROVIDER`/`DART_LLM_MODEL`) | Gemini 계열 (선택적 분류/패러프레이즈, 수치 생성 금지). BGE-M3/RAG는 현재 런타임 미연결 |
| 담당 | 성진 | 은진 |

### A-1 Dart_Analyzer 출력 예시

```
점수: 78점 / 방향: 긍정
주요 공시: 공급계약 체결 2건, 자사주 매입 1건
실적: 영업이익 컨센서스 대비 +12.3% (BEAT)
요약: 대규모 공급계약 공시 확인, 긍정 시그널
```

### A-2 Report_Analyzer 출력 예시

```
데이터 상태: partial
근거: 구조화 valuation fact와 정규화된 Report signal event
밸류에이션 fact: 목표가 원천값, 추정 EPS, 적용 배수, 내재 배수
데이터 방향성: 증권사 간 내재 배수 분산 확대, 카테고리 재평가 근거는 추가 확인 필요
주의: PDF에서 EPS 또는 적용 배수 누락 시 needs_review=true
```

### A-2 Report valuation 확장 원칙

- 목표주가 자체보다 목표주가 산정에 사용된 EPS, 적용 배수, 피어 그룹, 카테고리 가정을 우선 구조화한다.
- `implied_multiple = target_price / forward_eps_est`는 코드가 결정론적으로 계산한다.
- LLM은 methodology 분류, peer group 후보 정리, category tag, rerating thesis 패러프레이즈만 담당한다.
- LLM이 목표가, EPS, 배수, 점수 같은 수치를 생성하면 안 된다.
- `scenario_band`는 내재 배수 중앙값과 분산 기반 범위를 내부 구조화 값으로 저장한다.
- RAG 기반 evidence_chunks는 현재 런타임에 연결되어 있지 않으며, 복구 시 별도 설계와 테스트가 필요하다.
- 사용자-facing 출력은 Bear/Base/Bull을 투자 행동 제안이 아니라 데이터 시나리오 밴드로 표현한다.
- 상세 기준은 [`docs/spec/report-valuation-reinterpretation-strategy.md`](../spec/report-valuation-reinterpretation-strategy.md)를 따른다.

---

## 5.3 대안 데이터 분석기 (구조 분리) `[계획]`

대안 데이터는 채용·특허·검색 3종을 **각각 독립 분석**한 뒤, 별도 통합기가 묶는 구조다. 분석과 통합의 책임을 분리한다. **3종 모두 미구현 — 수집 데이터 적재와 `signal_events` 스키마는 준비되어 있다.**

```
Hiring_Analyzer   → hiring 신호   ┐
Patent_Analyzer   → patent 신호   ├→ Alt_Aggregator → alt 신호
DataLab_Analyzer  → datalab 신호  ┘
```

| 항목 | A-3a Hiring_Analyzer | A-3b Patent_Analyzer | A-3c DataLab_Analyzer |
| --- | --- | --- | --- |
| 입력 | hiring_raw_details (정규화 경유) | patent_raw_details (정규화 경유) | datalab_raw_details (정규화 경유) |
| 핵심 로직 | 직군 키워드 분류 → 변화율 산출 | 기술 카테고리 분류 → 신규 카테고리 = 피봇 징후 | 검색량 급등(50%↑) 감지 |
| 출력 이벤트 | HIRING_SPIKE / HIRING_DROP | NEW_PATENT_CATEGORY / PATENT_SPIKE | SEARCH_VOLUME_SPIKE / SEARCH_VOLUME_DROP |
| 사용 모델 | Flash-Lite (분류) | Flash-Lite (분류) | 룰 기반 (급등 판정) |

> 통합(Alt_Aggregator)에 대한 상세는 하위 문서 [06_통합단계_Aggregator.md](06_통합단계_Aggregator.md) 참조.

### 대안 분석 출력 예시

```
채용: HBM 관련 채용 18건, 전월 대비 +240%
특허: 온디바이스 AI 카테고리 첫 출원 감지 (신규 피봇 징후)
검색: '갤럭시 S25' 검색량 전주 대비 +180% 급등
```

---

## 5.4 A-5 Price_Analyzer — 구현

주가 수집기(C-6)가 적재한 `ohlcv_data`를 읽어, 다른 소스와 동일한 형식(방향 + 점수 + 근거)으로 변환한다. LLM 미사용, 전부 결정적 룰. (`app/analyzers/price/` — analyzer·indicators·rules)

### 포지셔닝

> Price는 선행 신호가 아니다. 다른 소스의 선행 신호를 **시장이 이미 반영했는지 검증**하는 역할이다. 선행 신호가 떴는데 주가가 안 움직였다면 기회 신호로 해석될 수 있다.

### 명세

| 항목 | 내용 |
| --- | --- |
| 입력 | ohlcv_data — DB만 읽음 (키움 API 직접 호출 금지) |
| LLM | 미사용. 결정적 룰 |
| 지표 | 이동평균 5/20/60, 골든/데드크로스, RSI(14), 거래량 z-score, 외인·기관 연속 순매수, 20일 변동성 |
| 점수 규칙 | 추세(±0.45) + 모멘텀 RSI(±0.1) + 수급(±0.35) → [-1,+1] 클램프. 추세·수급 강하게 충돌 시 mixed |
| risk_flags | overbought / oversold / high_volatility / volume_spike / stale_data / insufficient_history |
| 출력 | signal, score, risk_flags, indicators, summary |
| 담당 | 규태 |

> 0~100 점수 변환은 통합(D-1) 단계 책임으로 남긴다. 점수 정규화를 한 곳에 모아 일관성을 유지한다.
> 120영업일 백필 전까지는 `insufficient_history` 상태가 정상이다 (`docs/architecture.md`).

---

## 5.5 LLM 품질 관리

### AI틱 표현 방지

절대 사용 금지 표현:

```
종합적으로 / 전반적으로 / 다양한 / 복합적으로
감지되었습니다 / 포착되었습니다 / 확인되었습니다
우호적인 / 긍정적인 흐름 / 것으로 판단됩니다 / 것으로 보입니다
```

프롬프트 원칙: 3문장 이내, 각 문장에 수치 1개 이상, 수치는 절대 변경 금지. AI틱 표현 감지 시 재생성(최대 3회), 실패 시 템플릿 강제 출력.

구현: 금지·권장 표현 목록과 검증 로직은 `packages/signal-core/signal_core/safety.py`. 프롬프트 원문은 `services/agent-worker/app/prompts/`에서 버전 관리한다 (`dart_analysis_v1.md`).

### 수치 정확성 검증

1. 원본 수치가 출력 텍스트에 그대로 존재하는가
2. 점수 방향과 텍스트 표현이 일치하는가 (점수 70↑인데 "하락" 등장 → 오류)
3. 비정상 수치 포함 여부 (변화율 1,000% 초과 등)

검증 실패 시: 폴백 템플릿으로 강제 출력 (수치 오류 가능성 제로)

---

*상위 문서로 돌아가기 → [00_메인_기획서.md](00_메인_기획서.md)*
