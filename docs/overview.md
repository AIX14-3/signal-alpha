# Signal α — 제품 개요

## 한 줄 정의

**Signal α는 DART 공시, 증권사 리포트, Alternative Data(채용·특허·검색 트렌드 등)를 AI 에이전트가
수집·분석·교차검증하여, 개인 투자자에게 "여러 데이터 소스가 같은 방향을 가리키는지"와 그 근거를
보여주는 투자 정보 인텔리전스 서비스입니다.**

- 서비스명: **Signal α** (시그널 알파)
- 팀명: **Team LENS** — Link · Evidence · Navigate · Signal

> **이 서비스는 매수·매도·보유 추천 서비스가 아닙니다.** 자세한 문구 규칙은 아래
> [제품 문구 가드레일](#제품-문구-가드레일) 및 루트 `AGENTS.md`를 따릅니다.

## 문제 정의

개인 투자자는 DART 공시·분기 실적·증권사 리포트·채용공고·특허·검색 트렌드를 직접 찾아 해석해야 합니다.
**정보가 부족한 게 아니라, 무엇이 의미 있는 신호인지 판단하기 어렵다**는 것이 핵심 문제입니다.

> 핵심 질문: *"이 종목에 대해 여러 데이터가 정말 같은 방향을 가리키고 있는가?"*

## 타겟 사용자

- **주 타겟**: 직접 종목을 분석·판단하지만 여러 출처를 종합할 시간이 부족한 액티브 개인 투자자
- **부 타겟**: 정보 과부하를 느끼는 투자 입문자

## 멀티에이전트 개요

사용자가 종목을 입력하면, 표준화(종목명 → 종목코드/DART corp_code) 후 소스별 에이전트가 병렬 분석하고,
집계 단계가 결과를 통합합니다.

| 에이전트 | 소스 | 역할 |
|---|---|---|
| DART Watcher | 공시 | 공시 수집·유형 분류·고임팩트 공시 분석·실적 기반 신호 추출 |
| Report RAG | 증권사 리포트 | 리포트 메타/PDF RAG, 투자의견·목표주가·근거 추출, 증권사 간 의견 충돌 탐지 |
| Alternative Signal | 채용·특허·DataLab 등 | 사업 확장·R&D·수요 변화의 **흔적** 탐지 (긍/부정 단정 아님) |
| Debate Aggregation | (통합) | 긍정 근거 / 주의 근거를 분리 정리하고 소스 방향성 일치도 산출 |

집계 결과 핵심 필드: `consensus_score`, `alignment_rate`, `overall_direction`, `source_agreement`,
`positive_evidence`, `caution_evidence`, `needs_review`, `summary`.
용어 정의는 [glossary.md](./glossary.md), 흐름은 [data-pipeline.md](./data-pipeline.md), 집계 상세는
`spec/final-signal-aggregator-spec.md`를 참고하세요.

## 데이터 소스

| 소스 | 유형 | 수집 방식 |
|---|---|---|
| DART 공시 / 분기 실적 | 공식 | OpenDART API |
| 증권사 리포트 | 전문가 | 네이버 증권 리포트 목록 + 선별 PDF Local RAG (pgvector) |
| 채용공고 | Alternative | 채용 사이트 수집 |
| 특허 출원 | Alternative | KIPRIS 등 |
| 네이버 DataLab | Alternative | DataLab API (키워드 검색량) |

> 증권사 리포트 PDF 원문은 저작권 이슈로 **사용자에게 직접 노출하지 않으며**, 분석 결과 JSON과
> 원문 링크 중심으로 저장/제공합니다.

## 제품 문구 가드레일

본 서비스의 본질은 "추천"이 아니라 **"검증"**입니다. UI·API 응답·LLM 프롬프트·발표자료 전반에서 아래를 지킵니다.

**금지 표현**: 매수/매도/보유 추천, "지금 사야 한다", 상승 보장·목표 수익률·수익 예측, 추천 종목,
투자 타이밍 알림, 매집 구간, 단기 급등 가능성

**권장 표현**: 데이터 방향성, 소스 간 일치도, 근거, 데이터 정합성, 추가 확인 필요, 사용자 판단 보조

- 점수 용어는 `confidence`(투자 신뢰도로 오해 가능) 대신 `consensus_score` / `alignment_rate` /
  `source_agreement`를 사용합니다.
- **Signal Journal**은 사용자의 주관적 복기를 돕는 도구이며, 플랫폼이 투자 성과를 평가/추천하지 않습니다.

## 주요 리스크 대응

| 이슈 | 대응 |
|---|---|
| 투자자문업 오해 | 추천 없음. 소스 방향성 일치도와 근거만 제공 |
| 리포트 저작권 | PDF 원문 미노출, 분석 JSON·링크 중심 저장 |
| 크롤링 차단 | 배치 처리, 요청 간격 조절, User-Agent 설정 |
| LLM hallucination | 원문 evidence 기반 요약, JSON Schema 검증, 결정적 fallback |
| 비용 증가 | 고임팩트 데이터만 LLM 분석, 저임팩트는 메타데이터 저장 |

## BM 방향(초기)

종목 추천 판매가 아니라 **투자 정보 교차검증 SaaS / 인텔리전스 구독** 모델을 지향합니다
(B2C Freemium+Pro, Deep Dive 리포트 크레딧, B2B 데이터 방향성 API). 유료 추천주·수익률 보장·
성과보수형 과금은 지향하지 않습니다.

---

> 이 문서는 제품의 "왜/무엇"을 요약합니다. 시스템 구성은 [architecture.md](./architecture.md),
> 데이터 처리 흐름은 [data-pipeline.md](./data-pipeline.md)를 참고하세요.
> 더 방대한 과거 기획 원문은 `archive/project-context.md`에 보존되어 있습니다.
