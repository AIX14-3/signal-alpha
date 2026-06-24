# Signal α — Codex 개발 컨텍스트 정리

> 목적: 이 파일은 Codex 또는 다른 개발 환경에서 **Signal α 프로젝트를 바로 이어서 구현**할 수 있도록 만든 개발용 컨텍스트입니다.  
> 최신 기획 기준: 서비스명 **Signal α**, 팀명 **Team LENS**, 핵심 구조는 **멀티에이전트 기반 투자 정보 교차검증 서비스**입니다.

---

## 0. 한 줄 요약

**Signal α는 DART 공시, 증권사 리포트, Alternative Data를 AI 에이전트가 수집·분석·교차검증하여, 개인 투자자에게 “여러 데이터 소스가 같은 방향을 가리키는지”와 그 근거를 보여주는 투자 정보 인텔리전스 서비스입니다.**

중요: 이 서비스는 **매수·매도·보유 추천 서비스가 아닙니다.**  
UI, API 응답, LLM 프롬프트, 발표자료, 문구 모두 “투자 추천”이 아니라 **소스 방향성 일치도 / 데이터 근거 / 추가 확인 필요 여부**를 제공하는 방향으로 작성해야 합니다.

---

## 0-1. 현재 구현 상태 요약

> 기준일: 2026-06-12
> 이 섹션은 현재 레포의 실제 코드 구조를 기준으로 한 최신 개발 컨텍스트입니다. 아래의 오래된 기획 초안과 충돌할 경우 이 섹션과 `README.md`, `database/migrations/`, 각 서비스 README를 우선합니다.

### 현재 모노레포 구성

```text
final_project/
  web/                    # Next.js 기반 한국어 대시보드 UI
  services/
    main-server/           # FastAPI 사용자 API 서버
    agent-worker/          # FastAPI 내부 수집/분석 워커
  packages/
    data-access/           # DB repository 계층
    signal-core/           # 공통 안전/계약 패키지
  database/
    migrations/            # 실제 DB 스키마 기준
    docs/                  # DB 설계/책임 문서
  docs/                    # 기획, 아키텍처, 백로그, 백테스팅 문서
```

### 현재 서비스 책임

| 영역 | 현재 책임 |
|---|---|
| `web` | Next.js 앱 쉘, 메인 대시보드, 종목 추가 화면, 시그널 상세 화면의 한국어 UI |
| `main-server` | 외부 사용자 API, 헬스체크, 최신 시그널 조회, 향후 관심종목/저널/작업 API의 진입점 |
| `agent-worker` | 내부 워커 API, DART 수집/정규화/분석, Report/PRICE 분석, PRICE 가격 수집 데몬, 큐 실행, 스케줄 트리거 |
| `data-access` | DB 접근을 repository로 캡슐화. 서비스는 SQL을 직접 흩뿌리지 않고 repository를 우선 사용 |
| `database` | PostgreSQL + pgvector 스키마, seed, DB 테스트, 테이블 책임 문서 |

### 현재 핵심 실행 흐름

```text
[DART]
collect_dart
  -> OpenDART corp_code/list/document.xml 조회
  -> raw_documents + dart_raw_details 저장
  -> normalize_dart 큐 등록
  -> source_documents + signal_events + signal_metrics 생성
  -> analyze_dart 큐 등록
  -> rule 기반 SourceResult 생성
  -> 고임팩트 공시는 선택적으로 LLM 분석
  -> analysis_results + agent_results 저장

[PRICE]
agent-worker 내장 price collector
  -> stocks.is_target = TRUE 종목을 Kiwoom REST로 조회
  -> price_snapshots + ohlcv_data + collector_runs 저장
  -> agent-worker의 PRICE analyzer가 DB 데이터를 읽어 분석

[REPORT]
Report collector/analyzer
  -> report_raw_details / report_chunks / pgvector 기반 RAG 흐름을 사용
  -> 리포트 원문 PDF 자체를 사용자에게 노출하지 않고 요약/근거/링크 중심으로 사용

[MAIN/WEB]
web
  -> main-server API 호출
  -> main-server는 최종 signal 조회/사용자 기능을 담당
  -> agent-worker는 내부 API로만 수집/분석 작업을 수행
```

### 현재 구현된 주요 API

| 서비스 | 메서드/경로 | 용도 |
|---|---|---|
| main-server | `GET /health` | 메인 서버 헬스체크 |
| main-server | `GET /signals/{ticker}` | 종목별 최신 시그널 조회 |
| agent-worker | `GET /health` | 워커 헬스체크 |
| agent-worker | `POST /internal/tasks/{task_type}/enqueue` | `collect_dart`, `normalize_dart`, `analyze_dart` 작업 등록 |
| agent-worker | `POST /internal/tasks/{task_type}/run` | 작업 1건 실행 |
| agent-worker | `GET /internal/queue/tasks` | 큐 작업 목록 조회 |
| agent-worker | `POST /internal/queue/{task_type}/run-batch` | 특정 작업 타입 배치 실행 |
| agent-worker | `POST /internal/schedules/dart/collect` | DART 수집 스케줄 트리거 |
| agent-worker | `POST /internal/dart/corp-codes/sync` | DART corp code 동기화 |
| agent-worker | `GET /internal/dart/analysis-results` | DART 분석 결과 조회 |
| agent-worker | `GET /internal/dart/document-results` | DART 문서/분석 평탄화 조회 |
| agent-worker | `POST /internal/dart/e2e/run` | 개발용 DART 수집-정규화-분석 E2E 실행 |
| agent-worker | `POST /internal/price/analyze/{stock_code}` | DB 기반 PRICE 분석 실행 |
| agent-worker | `POST /internal/schedules/report/collect` | Report 수집 작업 등록 |
| agent-worker | `POST /internal/schedules/report/analyze` | Report 분석 작업 등록 |
| agent-worker | `POST /internal/schedules/report/normalize-backfill` | Report 정규화 backfill 작업 등록 |

### 현재 DB 흐름

실제 스키마 기준은 항상 `database/migrations/`입니다. 현재 MVP의 핵심 흐름은 다음 테이블을 중심으로 구성되어 있습니다.

```text
stocks
  -> raw_documents
  -> dart_raw_details / report_raw_details
  -> report_chunks
  -> source_documents
  -> signal_events
  -> signal_metrics
  -> analysis_results
  -> agent_results
  -> final_signals
```

운영/큐/수집 상태는 `processing_queue`, `collector_runs`, `dart_collection_states`, `validation_logs`를 사용합니다. 가격 데이터는 `price_snapshots`, `ohlcv_data`를 사용합니다.

### 현재 개발 원칙

1. mock/fixture는 테스트와 실패 fallback에만 사용하고, 기능 개발은 실제 수집 데이터 흐름을 우선한다.
2. 수집기와 분석기는 큐 작업 타입 기준으로 분리한다. DART는 `collect_dart`, `normalize_dart`, `analyze_dart`를 독립 작업으로 둔다.
3. LLM은 고임팩트 공시나 RAG 요약처럼 필요한 지점에만 사용하고, JSON 검증 실패/타임아웃/금지 표현 감지 시 rule 기반 fallback을 사용한다.
4. Kiwoom 가격 수집은 `agent-worker` 내장 price collector가 담당하고, PRICE analyzer는 DB만 읽는다.
5. 사용자-facing 문구는 “추천”이 아니라 “근거 확인”, “방향성”, “추가 확인 필요”를 중심으로 작성한다.

### 다음 구현 우선순위

1. DART `analysis_results`/`agent_results`를 `final_signals` 집계 흐름에 연결한다.
2. Report RAG의 실제 리포트 수집/청킹/임베딩/분석 경로를 완성한다.
3. PRICE 수집 데이터의 과거 OHLCV backfill과 PRICE analyzer 결과 저장을 연결한다.
4. main-server에 관심종목, 분석 실행, 최신 시그널 목록, 시그널 상세 API를 확장한다.
5. web 대시보드에서 실제 main-server API를 연결하고, 종목 추가-상세-근거 확인 흐름을 완성한다.
6. 충분한 시그널 이력이 쌓인 뒤 백테스팅/스코어링 검증을 진행한다.

---

## 1. 브랜드 / 네이밍

### 서비스명

- **Signal α**
- 한글 표기: **시그널 알파**

### 팀명

- **Team LENS**
- LENS 의미: **Link · Evidence · Navigate · Signal**
- 해석: 흩어진 데이터를 연결하고, 근거를 만들고, 방향을 제시하고, 의미 있는 신호로 제공한다.

### 포지셔닝 문장

> Signal α는 공시·리포트·Alternative Data를 멀티에이전트가 교차검증해 개인 투자자가 근거 있는 신호를 판단할 수 있도록 돕는 AI 투자 정보 인텔리전스 서비스입니다.

### 금지해야 할 포지셔닝

아래 표현은 투자자문처럼 보일 수 있으므로 피한다.

- AI 추천 종목
- 매수/매도 타이밍 제공
- 수익률 높은 종목 탐지
- 지금 사야 할 종목
- 상승 가능성 보장
- 목표 수익률 제시

### 권장 표현

- 소스 방향성 일치도
- 데이터 일치도
- 근거 기반 시그널
- 추가 확인 필요
- 공식 데이터와 Alternative Data의 교차 확인
- 사용자 판단 보조

---

## 2. 문제 정의

개인 투자자는 매일 다음과 같은 정보를 직접 찾아보고 해석해야 한다.

- DART 공시
- 분기 실적
- 증권사 리포트
- 채용공고
- 특허 출원
- 검색 트렌드

기관 투자자는 이런 데이터를 처리할 인력과 시스템이 있지만, 개인 투자자는 혼자서 정보를 찾고 비교해야 한다.

### 핵심 문제

정보가 부족한 것이 아니라, **무엇이 의미 있는 신호인지 판단하기 어렵다.**

### 핵심 질문

> 이 종목에 대해 여러 데이터가 정말 같은 방향을 가리키고 있는가?

Signal α는 이 질문에 답하기 위한 서비스다.

---

## 3. 타겟 사용자

### 주 타겟

- 직접 종목을 분석하고 투자 판단을 내리는 **액티브 개인 투자자**
- 공시나 리포트를 보긴 하지만, 여러 출처를 종합해 판단하는 데 시간이 부족한 사용자

### 부 타겟

- 투자 정보 과부하를 느끼는 투자 입문자
- 정보는 많지만 어떤 정보를 믿어야 할지 모르는 사용자

> 발표자료 최신 방향에서는 주타겟에 **나이 정보는 넣지 않는다.**

---

## 4. 핵심 사용자 플로우

```text
1. 사용자가 관심 종목 입력
   예: 삼성전자, SK하이닉스, 네이버

2. 서버가 종목명을 표준화
   예: 삼성전자 → 005930 / DART corp_code 매핑

3. 3개 분석 에이전트가 병렬 실행
   - DART Watcher Agent
   - Report RAG Agent
   - Alternative Signal Agent

4. Debate Aggregation Agent가 결과 통합
   - 긍정 근거와 주의 근거 정리
   - 소스 방향성 일치도 산출
   - needs_review 여부 판단

5. 대시보드 표시
   - 종목별 요약 카드
   - 소스별 방향성
   - 핵심 근거
   - 원문 링크

6. 사용자가 Signal Journal에 판단 기록
   - 플랫폼이 투자 성과를 평가하지 않고, 사용자의 주관적 복기를 돕는 용도
```

---

## 5. 데이터 소스

| 소스 | 유형 | 수집 방식 | 주요 의미 | MVP 우선순위 |
|---|---|---|---|---|
| DART 공시 | 공식 데이터 | OpenDART API | 기업 공식 이벤트, 실적, 주요사항 | 필수 |
| 분기 실적 | 공식 데이터 | DART API | 어닝 서프라이즈 여부 | 필수 |
| 증권사 리포트 | 전문가 데이터 | 네이버 증권 리포트 목록 + 로컬 PDF RAG | 목표주가, 투자의견, 전문가 근거 | 필수 |
| 채용공고 | Alternative Data | 사람인 크롤링 또는 API | 사업 확장 방향 선행 지표 | MVP 가능 |
| 특허 출원 | Alternative Data | KIPRIS API | R&D 방향, 기술 피봇 | MVP 가능 |
| 네이버 DataLab | Alternative Data | DataLab API | 소비자 수요 변화 선행 지표 | MVP 가능 |

### MVP 타깃 종목

우선 3개 종목 고정 운영:

- 삼성전자
- SK하이닉스
- 네이버

### 데이터 수급 원칙

1. **1주차에 데이터 수급 가능성부터 검증**한다.
2. 데이터 수집이 막히면 즉시 대체 전략으로 전환한다.
3. 크롤링은 요청 간격 조절, User-Agent 설정, 배치 처리 기반으로 구현한다.
4. 증권사 리포트 PDF 원문은 실서비스에서 저작권 이슈가 있으므로, MVP에서는 선별 PDF를 로컬 저장한 Local RAG로 처리한다.

---

## 6. 멀티에이전트 구조

```text
사용자 입력: stock_name / stock_code
        ↓
[FastAPI Main Server]
        ↓ HTTP
[FastAPI Agent Module / AI Worker]
        ↓
  ┌──────────────────────────────────────────┐
  │ Fan-out 병렬 실행                         │
  │                                          │
  │  Agent 1: DART Watcher                   │
  │  Agent 2: Report RAG                     │
  │  Agent 3: Alternative Signal             │
  └──────────────────────────────────────────┘
        ↓
Agent 4: Debate Aggregation
        ↓
최종 응답: source alignment + evidence + needs_review
        ↓
[FastAPI Main Server] 저장 / Journal / UI 전달
        ↓
[Next.js Dashboard]
```

---

## 7. Agent 1 — DART Watcher

### 담당

- 성진

### 역할

DART 공시 수집, 유형 분류, 주요 공시 분석, 실적 기반 신호 추출.

### 수집 방법

- OpenDART API 사용
- 타깃 종목 3개 우선 운영
- 수집 주기: 1일 1회 배치 + 주요 공시 감지 시 즉시 처리

### 처리 로직

- 고임팩트 유형만 LLM 분석
  - 주요사항보고
  - 실적 관련 공시
  - 자사주 매입
  - 공급계약
  - 유상증자/CB/BW 등 희석 가능 이벤트
  - 최대주주/임원 변동
  - 감사의견, 거래정지, 관리종목 등 리스크성 공시
- 저임팩트 공시는 제목과 링크만 저장
- 정정 공시 감지 시 원본 공시와 연결
- 분기 실적 + 증권사 컨센서스 비교로 어닝 서프라이즈 탐지

### 출력 JSON 예시

```json
{
  "source": "dart",
  "agent": "DART_WATCHER",
  "stock_code": "005930",
  "stock_name": "삼성전자",
  "score": 78,
  "direction": "positive",
  "evidence_items": [
    {
      "title": "대규모 공급계약 공시",
      "summary": "주요 계약 공시가 확인되었습니다.",
      "url": "https://dart.fss.or.kr/...",
      "published_at": "2026-06-01"
    }
  ],
  "earnings_surprise": {
    "type": "BEAT",
    "pct": 12.3
  },
  "risk_flags": [],
  "summary": "대규모 공급계약 공시 확인. 공식 데이터 기준 긍정 방향의 정보 변화입니다."
}
```

### 개발 시 주의

- DART는 `stock_code`만이 아니라 `corp_code` 매핑이 필요하다.
- `rcept_no` 기준으로 중복 저장 방지.
- 정정/철회/첨부정정 공시를 별도 이벤트로 처리하지 말고 원본 이벤트와 연결하는 구조가 좋다.
- 출력 문구에서 “매수 신호” 금지. “공식 데이터 기준 긍정 방향의 정보 변화”처럼 표현.

---

## 8. Agent 2 — Report RAG

### 담당

- 은진

### 역할

증권사 리포트 수집, PDF 파싱, 벡터 검색, 투자의견/목표주가/핵심 근거 추출, 증권사 간 의견 충돌 탐지.

### 수집 방법

1. 네이버 증권 리포트 목록 크롤링
   - 증권사명, 투자의견, 목표주가, 리포트 제목 등 메타데이터 확보
2. 로컬 PDF RAG
   - 선별 PDF 3~5개를 로컬에 저장
   - PyMuPDF로 텍스트 추출
   - 500토큰 chunking
   - BGE-M3 임베딩
   - pgvector 저장

### 처리 로직

- Top-K 검색 후 LLM으로 의견 추출
- 목표주가 평균과 현재 주가 괴리율 계산
- 최근 3개월 목표주가 상향/하향 트렌드 추적
- 증권사 간 목표주가 갭 25% 이상이면 `conflict_detected = true`

### 출력 JSON 예시

```json
{
  "source": "report",
  "agent": "REPORT_RAG",
  "stock_code": "005930",
  "stock_name": "삼성전자",
  "score": 68,
  "direction": "positive",
  "avg_target": 112000,
  "upside_pct": 31.8,
  "target_trend": "up",
  "conflict_detected": false,
  "opinions": [
    {
      "firm": "신한",
      "view": "매수",
      "target": 115000,
      "key_reason": "반도체 업황 회복 기대"
    }
  ],
  "summary": "최근 리포트는 목표주가 상향 흐름이 우세하지만, 일부 의견 차이는 추가 확인이 필요합니다."
}
```

### 개발 시 주의

- PDF 원문을 사용자에게 그대로 노출하지 않는다.
- DB에는 LLM 분석 결과 JSON과 원문 링크 중심으로 저장한다.
- 발표/서비스 문구에는 “데이터 제공 계약 필요”를 known issue로 명시 가능.

---

## 9. Agent 3 — Alternative Signal

### 담당

- 이슬

### 이름

최종 명칭은 **Alternative Signal Agent**를 권장한다.  
이전 이름 Social Signal은 유튜브/커뮤니티 느낌이 강하지만, 현재 기획은 채용·특허·DataLab 중심이므로 Alternative Signal이 더 적합하다.

### 역할

채용공고, 특허 출원, 네이버 DataLab 검색 트렌드를 수집해 **기업 변화와 수요 변화의 흔적**을 탐지한다.

### 핵심 철학

> 긍정/부정을 단정하는 것이 아니라, 수요 변화의 흔적을 찾는다.

### 세부 데이터

| 데이터 | 처리 방식 | 해석 |
|---|---|---|
| 채용공고 | 직군 키워드 분류, 전월 대비 변화율 | 사업 확장 방향, 실제 예산 집행 흔적 |
| 특허 출원 | 기술 카테고리 분류, 신규 카테고리 탐지 | R&D 방향, 기술 피봇 |
| 네이버 DataLab | 브랜드/제품 키워드 검색량 변화율 | 소비자 수요 변화 선행 신호 |

### 출력 JSON 예시

```json
{
  "source": "alternative",
  "agent": "ALTERNATIVE_SIGNAL",
  "stock_code": "000660",
  "stock_name": "SK하이닉스",
  "score": 74,
  "direction": "positive",
  "signals": [
    {
      "type": "hiring",
      "title": "HBM 관련 채용 증가",
      "change_pct": 240,
      "summary": "최근 30일 HBM 관련 채용 18건으로 전월 대비 크게 증가했습니다."
    },
    {
      "type": "patent",
      "title": "AI 메모리 관련 특허 증가",
      "change_pct": 85,
      "summary": "AI 메모리 관련 특허 출원이 증가했습니다."
    }
  ],
  "summary": "채용과 특허 데이터에서 사업 확장 방향과 관련된 변화 흔적이 확인됩니다."
}
```

### 개발 시 주의

- 채용/특허/검색 트렌드는 공식 투자 신호가 아니라 보조 선행 지표다.
- 출력 문구에서 “수요 폭발”, “확실한 성장”처럼 단정하지 않는다.
- 크롤링 실패 시 빈 배열과 `data_status: partial`을 반환하도록 설계한다.

---

## 10. Agent 4 — Debate Aggregation

### 담당

- 광현

### 역할

DART, Report, Alternative 세 에이전트의 결과를 종합하여 최종 대시보드용 결과를 만든다.

### 핵심 방식

단순 가중 평균이 아니라, 다음 두 관점을 분리해 정리한다.

- 긍정 근거: 데이터가 같은 방향을 보이는 팩트
- 주의 근거: 데이터 충돌, 표본 부족, 리포트 의견 차이, 검색 트렌드 약화 등

이후 Judge 단계에서 최종적으로 다음 값을 생성한다.

- `alignment_rate` 또는 `consensus_score`
- `source_agreement`
- `positive_evidence`
- `caution_evidence`
- `needs_review`
- `summary`

### 용어 원칙

- `confidence`라는 단어는 피한다.
- 대신 `consensus_score`, `alignment_rate`, `source_agreement`를 사용한다.
- 이유: confidence는 투자 조언의 신뢰도처럼 해석될 수 있음.

### 출력 JSON 예시

```json
{
  "stock_code": "000660",
  "stock_name": "SK하이닉스",
  "consensus_score": 82,
  "alignment_rate": "HIGH",
  "overall_direction": "positive",
  "source_agreement": {
    "dart": "positive",
    "report": "positive",
    "alternative": "positive"
  },
  "positive_evidence": [
    "DART 공식 공시에서 긍정 방향의 정보 변화가 확인되었습니다.",
    "리포트 목표주가 흐름이 상향입니다.",
    "HBM 관련 채용과 특허 데이터가 증가했습니다."
  ],
  "caution_evidence": [
    "검색 트렌드 표본 기간이 짧아 추가 확인이 필요합니다."
  ],
  "needs_review": false,
  "summary": "공식 데이터와 Alternative Data가 대체로 같은 방향을 보이고 있습니다. 단, 본 결과는 투자 추천이 아니라 데이터 방향성 분석입니다."
}
```

### 개발 시 주의

- LLM에게 투자 행위를 판단하게 하지 않는다.
- “매집 구간”, “매수 타이밍”, “상승 확실” 같은 표현 금지.
- 강세/약세 프롬프트도 투자 관점이 아니라 “데이터 팩트 관점”으로 제한한다.
- LLM 응답은 JSON Schema 또는 Pydantic 모델로 검증한다.
- LLM 실패 시 규칙 기반 fallback 요약을 제공한다.

---

## 11. Signal Journal

### 목적

사용자가 특정 시그널을 보고 어떤 판단을 했는지 기록하고, 나중에 복기할 수 있게 한다.

### 중요한 규제 원칙

Signal Journal은 플랫폼이 투자 성과를 평가하거나 추천하는 기능이 아니다.  
**오직 사용자의 주관적 복기를 돕는 도구**로 제한한다.

### 기록 항목 예시

```json
{
  "journal_id": 1,
  "user_id": 10,
  "stock_code": "005930",
  "signal_snapshot_id": 123,
  "user_decision": "watch",
  "memo": "공시와 리포트는 긍정이지만 검색 트렌드는 아직 약해 관망",
  "created_at": "2026-06-04T09:00:00+09:00"
}
```

### UI 문구 예시

- “이 시그널을 보고 어떤 생각을 했나요?”
- “나중에 다시 확인할 수 있도록 판단 근거를 기록해보세요.”
- “이 기능은 사용자의 주관적 복기를 돕기 위한 도구입니다.”

---

## 12. 기술 스택

### 기본 스택

| 영역 | 스택 |
|---|---|
| AI / Agent | Rule-based analyzer + 선택적 LLM 분석 + RAG |
| LLM Provider | 미정. Gemini/OpenAI 등 교체 가능한 Provider 추상화로 관리 |
| Embedding | BGE-M3, 1024 dim |
| Vector DB | PostgreSQL + pgvector |
| Worker Backend | FastAPI |
| Main Backend | FastAPI |
| Frontend | Next.js |
| DB Access | Python repository package (`packages/data-access`) |
| Infra | Docker Compose for local dev, 운영 DB는 별도 관리 가능 |
| FE 배포 | Vercel |
| CI/CD | GitHub Actions |

### 현재 서버 구조

현재 레포는 단일 `api-server`가 아니라 **FastAPI Main Server + FastAPI Agent Worker**로 나뉘어 있다. PRICE 수집 데몬은 별도 서비스가 아니라 `agent-worker` 안에서 실행된다.

```text
1. services/main-server
   - 외부 사용자 API
   - 대시보드/시그널 조회 API
   - 향후 관심종목, 저널, 분석 실행 요청 관리
   - 수집/분석 로직은 직접 들고 있지 않고 agent-worker 또는 DB 결과를 사용

2. services/agent-worker
   - 내부 수집/정규화/분석 API
   - DART, Report, PRICE, Alternative 계열 collector/analyzer/orchestrator
   - Kiwoom REST 기반 PRICE 수집 데몬
   - processing_queue 기반 작업 실행
   - LLM/RAG/JSON 검증/fallback 처리
```

### 이유

- Python은 AI/LLM/RAG/데이터 수집 생태계가 강함.
- Main Server와 Agent Worker를 FastAPI로 통일하면 Python 기반 schema와 repository를 공유하기 쉬움.
- 팀 규모와 MVP 기간을 고려해 별도 Java Main Server를 두지 않고 FastAPI로 백엔드를 통일함.
- 포트폴리오 관점에서는 “FastAPI 기반 API 서버, Agent orchestration, RAG, pgvector 연동” 경험을 명확히 보여줄 수 있음.

---

## 13. 추천 레포 구조

현재 레포 구조 기준.

```text
final_project/
  README.md
  docker-compose.yml
  .env.example

  web/
    package.json
    src/
      app/
      components/
      lib/

  services/
    main-server/
      app/
        main.py
        api/routes/health.py
        api/routes/signals.py
        core/config.py

    agent-worker/
      app/
        main.py
        api/routes/
          dart.py
          health.py
          price.py
          queue.py
          report.py
          schedules.py
          tasks.py
        collectors/
          dart/
          datalab/
          hiring/
          patent/
          price/     # Kiwoom REST 기반 내장 수집 데몬 + OHLCV reader
          report/
        analyzers/
          dart/
          price/
          report/
        orchestrator/
          dart/
          queue/
        prompts/

  packages/
    data-access/
      app/repositories/
    signal-core/

  database/
    migrations/
    docs/
    seeds/
    tests/

  docs/
    architecture.md
    data-flow.md
    project-context.md
    backlog.md
    backtesting-plan.md
```

---

## 14. 현재 API 설계

### FastAPI Main Server

#### `GET /health`

메인 서버 상태 체크.

#### `GET /signals/{ticker}`

종목별 최신 시그널 조회. 현재는 `SignalRepository.get_current_by_ticker`를 통해 DB의 최종 시그널을 읽는다.

### FastAPI Agent Worker

#### `GET /health`

워커 서버 상태 체크.

#### `POST /internal/tasks/{task_type}/enqueue`

큐 작업 등록. 현재 주요 task type은 `collect_dart`, `normalize_dart`, `analyze_dart`이다.

#### `POST /internal/tasks/{task_type}/run`

해당 타입의 큐 작업 1건을 즉시 실행한다.

#### `GET /internal/queue/tasks`

큐 작업 목록을 조회한다.

#### `POST /internal/queue/{task_type}/run-batch`

특정 작업 타입을 배치로 실행한다.

#### `POST /internal/schedules/dart/collect`

DART 수집 작업을 스케줄 기준으로 등록한다.

#### `POST /internal/dart/corp-codes/sync`

OpenDART corp code ZIP/XML 데이터를 동기화해 `dart_corp_codes`에 적재한다.

#### `GET /internal/dart/analysis-results`

DART 분석 결과를 조회한다.

#### `GET /internal/dart/document-results`

DART 원문/정규화/분석 결과를 개발 확인용으로 평탄화해 조회한다.

#### `POST /internal/dart/e2e/run`

개발 편의를 위해 DART 수집, 정규화, 분석을 한 번에 실행한다.

#### `POST /internal/price/analyze/{stock_code}`

DB에 적재된 가격 데이터를 기반으로 PRICE 분석을 실행한다.

#### `POST /internal/schedules/report/collect`

Report 수집 작업을 큐에 등록한다.

#### `POST /internal/schedules/report/analyze`

Report 분석 작업을 큐에 등록한다.

#### `POST /internal/schedules/report/normalize-backfill`

파싱 완료 후 canonical `source_documents`로 승격되지 않은 Report raw 문서를 `normalize_report` 작업으로 backfill한다.

---

## 15. 현재 DB 스키마 기준

> 실제 스키마의 유일한 기준은 `database/migrations/`입니다. 아래는 개발자가 빠르게 흐름을 이해하기 위한 요약입니다.

### 마스터/사용자

- `stocks`: 종목 코드, 종목명, DART corp_code, 타깃 여부 등 종목 마스터
- `users`: 사용자
- `user_watchlists`: 사용자 관심종목
- `signal_journals`: 사용자의 주관적 판단 기록

### 수집 원천

- `raw_documents`: 모든 수집 원문의 공통 헤더. `source_type`, `stock_code`, `external_id`, `collected_at` 등을 저장
- `dart_raw_details`: DART 공시 상세. `receipt_no`, 공시 제목, XML/텍스트 추출 결과, 정정 공시 관계 등을 저장
- `report_raw_details`: 증권사 리포트 원천 메타데이터
- `report_chunks`: RAG용 리포트 청크와 pgvector embedding
- `collector_runs`: 수집 실행 이력
- `dart_collection_states`: DART 증분 수집 상태

### 정규화/분석

- `source_documents`: 원천 문서를 분석 가능한 표준 문서로 정규화한 결과
- `signal_events`: 공시/리포트/가격 등에서 추출한 이벤트 단위 신호
- `signal_metrics`: 이벤트에서 추출한 수치형/정성형 지표
- `analysis_results`: 소스별 분석 결과
- `agent_results`: 에이전트 실행 결과와 근거 연결
- `final_signals`: 사용자에게 보여줄 최종 시그널
- `validation_logs`: 정규화/분석 검증 및 fallback 로그

### 큐/가격/확장

- `processing_queue`: 수집, 정규화, 분석 작업 큐
- `price_snapshots`: 장중 가격 스냅샷
- `ohlcv_data`: 일봉/거래량/수급 등 가격 분석 데이터
- `backtest_results`, `score_history`, `ml_scores` 등: 백테스팅/스코어링 확장용

### 현재 MVP 데이터 흐름

```text
stocks
  -> raw_documents
  -> dart_raw_details / report_raw_details
  -> report_chunks
  -> source_documents
  -> signal_events
  -> signal_metrics
  -> analysis_results
  -> agent_results
  -> final_signals
```

---

## 16. 공통 타입 정의 예시

TypeScript / Python Pydantic 모두 아래 구조를 기준으로 맞추면 좋다.

```ts
type Direction = "positive" | "neutral" | "negative" | "mixed" | "unknown";

type SourceType = "dart" | "report" | "price" | "alternative";

type EvidenceItem = {
  title: string;
  summary: string;
  url?: string;
  published_at?: string;
  source_name?: string;
};

type SourceResult = {
  source: SourceType;
  agent: string;
  stock_code: string;
  stock_name: string;
  score?: number;
  direction: Direction;
  summary: string;
  evidence_items: EvidenceItem[];
  risk_flags: string[];
  data_status?: "ok" | "partial" | "failed";
};

type AggregatedSignal = {
  stock_code: string;
  stock_name: string;
  consensus_score: number;
  alignment_rate: "HIGH" | "MEDIUM_HIGH" | "MEDIUM" | "LOW";
  overall_direction: Direction;
  source_agreement: Record<SourceType, Direction>;
  positive_evidence: string[];
  caution_evidence: string[];
  needs_review: boolean;
  summary: string;
};
```

---

## 17. UI 화면 구성

### 필수 화면

1. Landing / Intro
   - 서비스 소개
   - “매수/매도 추천이 아닌 데이터 교차검증 서비스” 문구 포함

2. Watchlist Dashboard
   - 관심 종목 카드
   - consensus_score
   - alignment_rate
   - source별 방향성
   - needs_review 배너

3. Signal Detail
   - DART / Report / Alternative 탭
   - 각 source의 evidence 목록
   - 원문 링크
   - 긍정 근거 / 주의 근거

4. Signal Journal
   - 사용자의 판단 기록
   - 메모 입력
   - “주관적 복기 도구” 고지

### 대시보드 카드 예시

```text
SK하이닉스
데이터 방향성 일치도: HIGH
종합 방향: positive

DART: positive
Report: positive
Alternative: positive

핵심 근거
- HBM 관련 채용 증가
- 리포트 목표주가 상향 흐름
- 공식 공시에서 긍정 방향 정보 변화 확인

주의 근거
- 검색 트렌드 표본 기간이 짧음
```

---

## 18. LLM 프롬프트 원칙

### 공통 시스템 규칙

LLM 프롬프트에는 반드시 다음 원칙을 넣는다.

```text
너는 투자 추천을 하지 않는다.
매수, 매도, 보유, 목표 수익률, 가격 전망을 단정하지 않는다.
오직 제공된 데이터의 사실, 방향성, 소스 간 일치 여부만 설명한다.
불확실하거나 데이터가 부족하면 추가 확인 필요라고 말한다.
출력은 반드시 지정된 JSON Schema를 따른다.
```

### 금지 표현

- 매수하세요
- 지금 사야 합니다
- 매도해야 합니다
- 목표 수익률
- 상승 확실
- 매집 구간
- 추천 종목
- 단기 급등 가능성

### 권장 표현

- 공식 데이터 기준 긍정 방향의 정보 변화
- 리포트 의견은 일부 엇갈림
- Alternative Data에서 수요 변화의 흔적 확인
- 소스 간 방향성이 대체로 일치
- 추가 확인이 필요한 상태

---

## 19. 리스크 대응

| 이슈 | 대응 |
|---|---|
| 투자자문업 오해 | 매수/매도 추천 없음. 소스 방향성 일치도와 근거만 제공 |
| 증권사 리포트 저작권 | PDF 원문 미노출. 분석 결과 JSON과 링크 중심 저장 |
| 크롤링 IP 차단 | 배치 처리, 요청 간격 조절, User-Agent 설정 |
| DataLab API 정책 변경 | 대체 소스 준비, 실패 시 partial data 처리 |
| LLM hallucination | 원문 evidence 기반 요약, JSON Schema 검증, fallback 처리 |
| LLM 응답 비결정성 | temperature 낮게 설정, 테스트 fixture 사용, 규칙 기반 fallback |
| 비용 증가 | 고임팩트 데이터만 LLM 분석, 저임팩트 데이터는 메타데이터 저장 |

---

## 20. 구현 우선순위

### 현재 1차 우선순위

1. DART 수집-정규화-분석 결과를 `final_signals` 집계 흐름에 연결
2. Report RAG 실제 데이터 파이프라인 완성
   - 리포트 원천 수집
   - 청킹
   - embedding
   - pgvector 검색
   - 분석 결과 저장
3. PRICE 수집 데이터 분석 연결
   - 과거 OHLCV backfill
   - PRICE analyzer 결과 저장
   - DART/Report 결과와 같은 최종 시그널 모델로 통합
4. main-server API 확장
   - 관심종목 등록/조회
   - 분석 실행 요청
   - 최신 시그널 목록
   - 시그널 상세
   - Signal Journal
5. web API 연동
   - 대시보드 카드
   - 종목 추가
   - 소스별 근거 패널
   - 시그널 상세
6. 실제 수집 데이터 기반 E2E 테스트 정리

### 2차 구현

1. KIPRIS, DataLab, 채용공고 등 Alternative 수집기 확장
2. 수집 주기와 분석 주기 분리 설정
3. 스케줄러 운영 정책 정리
4. 실패/재시도/부분 성공 상태 UI 표시
5. source별 드릴다운 패널 고도화
6. 시그널 급변 알림

### 후순위 / 발표 후 확장

1. 백테스트
2. 사용자별 개인화 추천
3. 포트폴리오 연동
4. 실시간 전 종목 모니터링
5. 유료 BM 적용

---

## 21. 6주 로드맵

| 주차 | 목표 | 검증 항목 |
|---|---|---|
| 1주차 | DART 실제 수집/정규화/분석 안정화 | corp_code 동기화, document.xml 텍스트 추출, queue 재시도 |
| 2주차 | DART 결과 최종 시그널 통합 | analysis_results/agent_results/final_signals 연결 |
| 3주차 | Report RAG 실제 데이터 연결 | 리포트 수집, 청킹, embedding, pgvector 검색, 분석 저장 |
| 4주차 | PRICE 수집/분석 연결 | Kiwoom REST 수집, OHLCV backfill, PRICE analyzer 결과 저장 |
| 5주차 | main-server/web E2E | 종목 추가, 분석 실행, 최신 시그널, 상세 근거, Signal Journal |
| 6주차 | 발표/검증 정리 | 데모 시나리오, 실패 fallback, 백테스팅 기획, 운영 리스크 |

---

## 22. BM 방향

초기 BM은 종목 추천 판매가 아니라 **투자 정보 교차검증 SaaS / 인텔리전스 구독 모델**로 잡는다.

### 추천 BM

1. B2C Freemium + Pro 구독
   - 무료: 관심 종목 3개, 간단 요약
   - Pro: 관심 종목 확대, source별 근거, 급변 알림, Signal Journal 고급 기능

2. Deep Dive 리포트 크레딧
   - 특정 종목에 대한 공시·리포트·Alternative Data 심화 분석

3. Signal Journal Premium
   - 사용자의 주관적 판단 기록과 복기 리포트
   - 플랫폼 차원의 투자 성과 평가 금지

4. B2B SaaS / API
   - 증권사, 핀테크, 투자 교육 플랫폼에 데이터 방향성 API 제공

### 피해야 할 BM

- 유료 추천주
- 매수/매도 타이밍 알림
- 수익률 보장
- 성과보수형 과금
- 리포트 PDF 원문 판매

---

## 23. 발표자료 최신 결정사항

최근 PPT 작업에서 반영된 디자인/구성 결정:

- 참고 PPT 스타일을 반영한 밝고 차분한 카드형 디자인
- 목차 페이지 포함
- 나이 정보는 타겟 페이지에서 삭제
- MVP 데모 페이지는 제외
- 스코어링 원칙/공식은 발표자료에서 삭제
- 기술 스택 페이지 포함
- 차별점, 리스크 대응, 기술 스택, 로드맵은 각각 분리해 가독성 확보
- 마지막 Thank You / Q&A 페이지 포함

개발 문서에서는 내부 구현용으로 `consensus_score`를 사용할 수 있지만, 발표자료나 사용자 문구에서는 **스코어링 공식**을 드러내지 않는 방향이 좋다.

---

## 24. Codex에게 바로 줄 수 있는 작업 프롬프트 예시

아래 프롬프트를 Codex에 붙여 넣고 이어가면 된다.

```text
Signal α 프로젝트를 구현하려고 한다.
이 프로젝트는 DART 공시, 증권사 리포트, Alternative Data를 멀티에이전트가 분석하고, 소스 방향성 일치도를 보여주는 투자 정보 교차검증 서비스다.
매수/매도 추천은 절대 하지 않는다.

우선 monorepo 구조로 다음을 만들어줘.

1. services/main-server: FastAPI 기반 Python Main Server
   - /health
   - /signals/{ticker}
   - 관심종목 API
   - 분석 실행 요청 API
   - 최신 시그널 목록/상세 API
   - Signal Journal API
   - agent-worker 내부 API 호출

2. services/agent-worker: FastAPI 기반 내부 Worker
   - /internal/tasks/{task_type}/enqueue
   - /internal/tasks/{task_type}/run
   - /internal/queue/*
   - /internal/dart/*
   - /internal/price/analyze/{stock_code}
   - /internal/schedules/report/*
   - DART collect/normalize/analyze 작업 분리
   - Report collect/process/normalize/embed/analyze 작업 분리
   - Kiwoom REST 기반 PRICE 수집 데몬 내장
   - PRICE DB 기반 분석
   - 각 analyzer는 실제 수집/저장 데이터를 기반으로 동작하게 구현
   - Aggregation은 confidence 대신 consensus_score/alignment_rate 사용
   - 금지 표현 필터를 포함

3. web: Next.js dashboard skeleton
   - 관심 종목 카드
   - source별 방향성
   - positive/caution evidence
   - Signal Journal 입력 폼

4. docker-compose.yml
   - postgres + pgvector
   - main-server
   - agent-worker
   - web

테스트 가능한 최소 실행 상태로 만들어줘.
```

---

## 25. 최종 주의사항

- 이 프로젝트의 본질은 “추천”이 아니라 “검증”이다.
- `confidence`보다 `consensus_score`, `alignment_rate`를 사용한다.
- LLM 출력은 JSON Schema로 검증한다.
- PDF 원문 노출 금지.
- Signal Journal은 사용자의 주관적 복기 도구로 제한한다.
- 데이터 수급 검증이 1순위다.
- 처음부터 완벽한 실시간 서비스를 만들지 말고, 3개 종목의 실제 수집 데이터를 우선 연결하고 실패 source는 partial/fallback 상태로 처리한다.
