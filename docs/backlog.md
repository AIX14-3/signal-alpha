# Signal α Vibe Coding Backlog

> 목적: `docs/` 컨텍스트를 바탕으로 Signal α를 바로 구현하기 위한 작업 백로그입니다.  
> 원칙: 이 서비스는 **매수·매도·보유 추천 서비스가 아니라 데이터 소스 방향성 교차검증 서비스**입니다.

---

## 0. 핵심 구현 원칙

- 사용자 문구, API 응답, LLM 프롬프트에서 투자 추천 표현을 금지한다.
- `confidence` 대신 `consensus_score`, `alignment_rate`, `source_agreement`를 사용한다.
- Collector는 외부 데이터를 가져와 `raw_evidence`만 만든다.
- Analyzer는 `raw_evidence`를 읽어 `source_results`만 만든다.
- Orchestrator는 job 상태, 수집 실행, 분석 실행, aggregation 순서만 조율한다.
- DART / Report / Alternative 수집과 분석 결과는 코드, DB, 테스트에서 분리한다.
- MVP는 삼성전자, SK하이닉스, 네이버 3개 종목의 실제 수집 데이터를 우선 사용한다.
- mock/fixture는 주요 개발 경로가 아니라 테스트, 장애 재현, fallback 검증 용도로만 사용한다.
- 실제 데이터 수집이 막히면 해당 source를 `partial`로 표시하고, 가능한 source 결과만으로 분석/통합을 계속한다.

---

## 1. MVP 목표

사용자가 관심 종목을 등록하고 분석 실행 버튼을 누르면, 메인 서버가 분석 작업을 만들고 에이전트 워커를 호출해 다음 결과를 대시보드에 표시한다.

- 종목별 최신 `AggregatedSignal`
- DART / Report / Alternative source별 방향성
- 긍정 근거와 주의 근거
- `needs_review` 표시
- Signal Journal 작성/조회

---

## 2. Epic Backlog

> 각 Epic 아래 `🔗 GitHub 이슈` 줄은 해당 작업의 GitHub 이슈 연결점입니다. 이슈 본문에도 이 파일(`docs/backlog.md`)의 Epic 섹션이 링크되어 양방향으로 추적됩니다.
> 별도 트랙: C-4/C-4-1 price-collector — OpenAPI+(Windows 전용) 구현(#16 · #27, PR #26 · #32)은
> 도커 리눅스 실행 불가로 **revert**되었고, 키움 REST API 실시간 폴링 수집기로 재구현됨
> (agent-worker 내장 데몬으로 통합 — `docs/price-collector.md`, `docs/kiwoom-rest-spec.md` 참고). 120일 과거 백필은 후속 작업.
> 참고: price 트랙 수집기는 `raw_evidence`가 아니라 시세 전용 테이블(`price_snapshots`,
> `ohlcv_data`)에 직접 적재하는 예외 트랙이다 (수치 시계열은 evidence 모델과 맞지 않음).

### E0. 레포 실행 골격 만들기

> 🔗 GitHub 이슈: #1 · #2 · #3 · #4 (모두 완료)

**목표**: monorepo를 로컬에서 한 번에 실행 가능한 상태로 만든다.

- [ ] `services/main-server` FastAPI 앱 생성
- [ ] `services/agent-worker` FastAPI 앱 생성
- [ ] `web` Next.js 앱 생성
- [ ] 루트 `docker-compose.yml` 추가
- [ ] 루트 `.env.example` 추가
- [ ] 서비스별 `README.md`에 실행 명령 업데이트
- [ ] 기본 헬스체크 연결

**완료 기준**

- [ ] `GET /health`가 main-server와 agent-worker에서 응답한다.
- [ ] `docker compose up` 또는 로컬 명령으로 세 서비스가 실행된다.

---

### E1. 공유 도메인 타입 정의

> 🔗 GitHub 이슈: #5 · #6 (모두 완료)

**목표**: 백엔드와 프론트가 동일한 시그널 계약을 사용한다.

- [ ] `SourceType`: `dart`, `report`, `alternative`
- [ ] `Direction`: `positive`, `neutral`, `negative`, `mixed`, `unknown`
- [ ] `EvidenceItem` 정의
- [ ] `RawEvidence` 정의
- [ ] `AnalysisResult` 정의
- [ ] `SourceResult` 정의
- [ ] `AggregatedSignal` 정의
- [ ] `AnalysisJobStatus` 정의
- [ ] 금지 표현 필터용 상수 정의

**완료 기준**

- [ ] main-server, agent-worker, web에서 같은 필드명을 사용한다.
- [ ] `confidence`라는 필드명이 코드와 API 응답에 등장하지 않는다.

---

### E2. 데이터베이스와 저장 계층

> 🔗 GitHub 이슈: #13 · #24 · #28 · #30 (모두 완료)

**목표**: 수집 원천, 분석 결과, 작업 상태, 저널을 분리 저장한다.

- [ ] PostgreSQL + pgvector compose 구성
- [ ] 마이그레이션 도구 선택 및 초기화
- [ ] `users` 테이블 생성
- [ ] `stocks` 테이블 생성
- [ ] `watchlists` 테이블 생성
- [ ] `analysis_jobs` 테이블 생성
- [ ] `raw_evidence` 테이블 생성
- [ ] `analysis_results` 또는 `source_results` 테이블 생성
- [ ] `signal_snapshots` 테이블 생성
- [ ] `signal_journals` 테이블 생성
- [ ] `report_chunks` 테이블 생성
- [ ] 삼성전자 / SK하이닉스 / 네이버 seed 데이터 추가

**완료 기준**

- [ ] Collector 결과는 `raw_evidence`에만 저장된다.
- [ ] Analyzer 결과는 `source_results`에만 저장된다.
- [ ] Aggregation 결과는 `signal_snapshots`에만 저장된다.
- [ ] Journal은 `signal_snapshot_id`를 기준으로 연결된다.

---

### E3. Main Server API

> 🔗 GitHub 이슈: #61 · #62 · #63 · #64

**목표**: 프론트엔드가 직접 호출하는 사용자-facing API를 만든다.

- [ ] `GET /health`
- [ ] `POST /api/watchlists`
- [ ] `GET /api/watchlists`
- [ ] `DELETE /api/watchlists/{stockCode}`
- [ ] `POST /api/signals/run/{stockCode}`
- [ ] `GET /api/signals/latest`
- [ ] `GET /api/signals/{signalId}`
- [ ] `POST /api/journals`
- [ ] `GET /api/journals`
- [ ] agent-worker 호출 클라이언트 구현
- [ ] 외부 호출 실패 시 fallback snapshot 반환

**완료 기준**

- [ ] 프론트는 main-server만 호출한다.
- [ ] 분석 실행 API가 job 생성, worker 호출, snapshot 저장까지 완료한다.

---

### E4. Agent Worker Pipeline Boundary

> 🔗 GitHub 이슈: #7(완료) · #8 · #9 · #65

**목표**: 수집, 분석, 통합 단계를 명확히 분리해서 실행한다.

- [ ] `POST /agents/analyze`
- [ ] `POST /agents/dart`
- [ ] `POST /agents/report`
- [ ] `POST /agents/alternative`
- [ ] `collectors/`, `analyzers/`, `orchestrator/` 디렉터리 분리
- [ ] 실제 수집 task와 분석 task를 별도 queue로 분리
- [ ] collect 단계 fan-out 구현
- [ ] 저장된 raw data 기반 analyze 단계 fan-out 구현
- [ ] aggregation 단계 구현
- [ ] partial 실패 처리
- [ ] 규칙 기반 aggregation fallback 구현
- [ ] JSON schema/Pydantic 검증
- [ ] 금지 표현 필터 적용

**완료 기준**

- [ ] Collector는 `direction`, `score`, `summary`를 만들지 않는다.
- [ ] Analyzer는 외부 API나 크롤링을 직접 호출하지 않는다.
- [ ] `POST /agents/analyze`가 `raw_evidence_ids`, `agent_results`, `aggregation`을 반환한다.
- [ ] 일부 agent 실패 시에도 `data_status: partial`과 `needs_review: true`를 반환한다.

---

### E5. DART Collector + Analyzer

> 🔗 GitHub 이슈: #35(완료) · #51

**목표**: 공식 공시 raw evidence와 DART source result를 분리해 만든다.

**Collector 작업**

- [ ] OpenDART API 키 설정
- [ ] stock_code → corp_code 매핑 로직 구현
- [ ] 3개 MVP 종목 공시 목록 조회 검증
- [ ] `rcept_no` 기준 중복 방지
- [ ] 정정/철회 공시를 원본 이벤트와 연결할 raw metadata 저장
- [ ] 공시 제목, 접수번호, URL, 제출일을 `raw_evidence`로 저장
- [ ] API 실패/무데이터/기간 오류 상태를 retry 또는 partial 상태로 기록

**Analyzer 작업**

- [ ] 고임팩트 공시 유형 분류
- [ ] 실제 저장된 DART raw 문서 기반 분석 입력 구성
- [ ] 공식 데이터 기준 방향성 산출
- [ ] DART source result 생성

**완료 기준**

- [ ] 삼성전자, SK하이닉스, 네이버 MVP 종목의 실제 DART 호출과 DB 적재가 성공한다.
- [ ] DART Collector 실패 시 fixture 대신 실패 상태와 `partial` 판단 근거가 기록된다.
- [ ] DART Analyzer는 저장된 `raw_evidence`만으로 결과를 만든다.

---

### E6. Report Collector + Analyzer

> 🔗 GitHub 이슈: #37 · #38 · #66

**목표**: 리포트 수집/인덱싱과 RAG 분석을 분리한다.

**Collector / Ingestion 작업**

- [ ] 네이버 증권 리포트 목록 수집 가능성 검증
- [ ] 로컬 PDF 저장 위치 결정
- [ ] PyMuPDF 텍스트 추출 구현
- [ ] 500토큰 chunking 구현
- [ ] BGE-M3 embedding 설정
- [ ] pgvector 저장/검색 구현
- [ ] 리포트 제목, 증권사, 발행일, 링크를 `raw_evidence`로 저장
- [ ] PDF chunk는 `report_chunks`에 저장

**Analyzer 작업**

- [ ] Top-K 검색 후 의견/목표주가/근거 추출
- [ ] 목표주가 갭 25% 이상 conflict 감지
- [ ] 최근 3개월 목표주가 상향/하향 트렌드 산출
- [ ] Report source result 생성
- [ ] PDF 원문 미노출 보장

**완료 기준**

- [ ] 로컬 PDF 1개 이상으로 RAG 검색 결과가 나온다.
- [ ] Report Analyzer는 `raw_evidence`와 `report_chunks`만 읽는다.
- [ ] Report 결과는 요약 JSON과 원문 링크/메타데이터만 노출한다.

---

### E7. Alternative Collector + Analyzer

> 🔗 GitHub 이슈: #18(완료) · #20(완료) · #39(완료) · #40 · #67 · #46 · #47 · #48 · #49 · #68

**목표**: Alternative Data 원천 수집과 변화 해석을 분리한다.

**Collector 작업**

- [ ] MVP 첫 source 선택: DataLab 또는 KIPRIS
- [ ] API/크롤링 접근 가능성 검증
- [ ] 실패 시 `data_status: partial` 반환
- [ ] 실패/부분 데이터 상태와 원인 metadata 저장
- [ ] 원천 count, time-series, keyword, URL을 `raw_evidence`로 저장

**Analyzer 작업**

- [ ] 전월 대비 변화율 계산
- [ ] signal type별 결과 정규화
- [ ] 변화 흔적 중심 summary 생성
- [ ] Alternative source result 생성

**완료 기준**

- [ ] 최소 1개 Alternative source가 실제 호출로 동작한다.
- [ ] Alternative Analyzer는 외부 수집을 하지 않고 저장된 raw data만 해석한다.
- [ ] 단정 표현 없이 “변화 흔적” 중심으로 요약한다.

---

### E8. Debate Aggregation Agent

> 🔗 GitHub 이슈: #69 · #70

**목표**: 세 source 결과를 대시보드용 최종 시그널로 통합한다.

- [ ] `raw_evidence`가 아니라 `source_results`만 입력으로 사용
- [ ] source별 direction 수집
- [ ] `source_agreement` 생성
- [ ] `consensus_score` 계산 규칙 정의
- [ ] `alignment_rate` 산출 규칙 정의
- [ ] 긍정 근거와 주의 근거 분리
- [ ] 데이터 충돌 시 `needs_review: true`
- [ ] LLM 실패 시 규칙 기반 fallback summary 생성
- [ ] 투자 추천 금지 표현 검증

**완료 기준**

- [ ] 세 agent 결과에서 `AggregatedSignal`이 안정적으로 생성된다.
- [ ] 혼합/충돌 데이터에서는 주의 근거가 반드시 표시된다.

---

### E9. Web Dashboard

> 🔗 GitHub 이슈: #41(완료) · #71 · #72 · #15 · #80

**목표**: 사용자가 시그널을 한 화면에서 빠르게 확인하게 만든다.

- [ ] Landing / Intro 화면
- [ ] “매수/매도 추천이 아닌 데이터 교차검증 서비스” 고지
- [ ] Watchlist Dashboard 화면
- [ ] 관심 종목 카드 컴포넌트
- [ ] `consensus_score` / `alignment_rate` 표시
- [ ] source별 direction badge 표시
- [ ] `needs_review` 배너 표시
- [ ] 분석 실행 버튼
- [ ] loading/error/partial 상태 UI
- [ ] Zustand 또는 query state 구성

**완료 기준**

- [ ] seed 3개 종목 카드가 표시된다.
- [ ] 분석 실행 후 최신 시그널 카드가 갱신된다.

---

### E10. Signal Detail

> 🔗 GitHub 이슈: #73

**목표**: 사용자가 source별 근거를 드릴다운해 확인하게 만든다.

- [ ] Signal detail route 생성
- [ ] DART / Report / Alternative 탭
- [ ] source별 evidence 목록
- [ ] 원문 링크 표시
- [ ] 긍정 근거 섹션
- [ ] 주의 근거 섹션
- [ ] partial data 안내
- [ ] 추천 아님 고지 문구

**완료 기준**

- [ ] source별 근거와 최종 aggregation 근거가 분리되어 보인다.
- [ ] PDF 원문 전문은 노출하지 않는다.

---

### E11. Signal Journal

> 🔗 GitHub 이슈: #74

**목표**: 사용자의 주관적 판단 기록과 복기를 지원한다.

- [ ] Journal 작성 API 연동
- [ ] Journal 목록 API 연동
- [ ] `user_decision`: `watch`, `research_more`, `ignore` 등 결정
- [ ] memo 입력 폼
- [ ] signal snapshot 연결
- [ ] “주관적 복기 도구” 고지
- [ ] 수정/삭제는 2차로 분리

**완료 기준**

- [ ] 특정 signal snapshot에서 Journal을 작성할 수 있다.
- [ ] 작성된 Journal 목록을 다시 조회할 수 있다.

---

### E12. 안전장치와 품질

> 🔗 GitHub 이슈: #75 · #77

**목표**: 투자 추천 오해와 LLM/외부 데이터 실패를 방지한다.

- [ ] 금지 표현 필터 테스트
- [ ] JSON Schema/Pydantic validation 테스트
- [ ] 실제 수집 데이터 기반 worker 핵심 경로 테스트
- [ ] aggregation 충돌 케이스 테스트
- [ ] API 통합 테스트
- [ ] 외부 API timeout/retry 설정
- [ ] User-Agent와 요청 간격 설정
- [ ] LLM temperature 낮게 고정
- [ ] 비용 방어: 고임팩트 데이터만 LLM 분석

**완료 기준**

- [ ] 금지 표현이 포함된 결과는 저장/응답 전에 차단 또는 수정된다.
- [ ] LLM/API 실패 상황에서도 대시보드가 fallback 결과를 보여준다.

---

### E13. 배포와 CI/CD

> 🔗 GitHub 이슈: #76 · #78 · #79

**목표**: 서비스별 독립 배포와 기본 검증 파이프라인을 만든다.

- [ ] Dockerfile: main-server
- [ ] Dockerfile: agent-worker
- [ ] Dockerfile: web
- [ ] GitHub Actions path filter 구성
- [ ] main-server 테스트 workflow
- [ ] agent-worker 테스트 workflow
- [ ] web lint/build workflow
- [ ] Vercel web 배포 설정
- [ ] EC2 Docker Compose 배포 문서 작성

**완료 기준**

- [ ] frontend 변경이 backend 재배포를 트리거하지 않는다.
- [ ] 각 서비스가 독립적으로 build/test 된다.

---

## 3. 추천 구현 순서

### Sprint 0 — 오늘 바로 시작

1. FastAPI main-server skeleton
2. FastAPI agent-worker skeleton
3. Next.js web skeleton
4. shared schema 정의
5. DB compose + seed stocks
6. Collector / Analyzer / Orchestrator 경계 분리
7. DART corp code sync와 실제 수집 task 준비
8. web 카드가 실제 API 응답을 받을 자리 준비

### Sprint 1 — 실제 DART 수집·정규화 루프

1. watchlist CRUD
2. DART Collector 실제 호출 검증
3. DART raw evidence 저장
4. `normalize_dart` queue 실행
5. DART signal event / source result 생성
6. signal run endpoint
7. latest signal API
8. dashboard update
9. journal create/list

### Sprint 2 — Report / Alternative 실제 데이터 연결

1. Report Collector local PDF ingestion
2. Report Analyzer RAG 검증
3. Alternative Collector source 1개 실제 호출 검증
4. Alternative Analyzer 변화율 검증
5. source별 SourceResult 생성
6. partial/fallback 처리
7. Debate Aggregation 통합
8. source detail 화면

### Sprint 3 — 발표 가능한 완성도

1. 삼성전자 / SK하이닉스 / 네이버 실제 수집 데이터 점검
2. Debate Aggregation 품질 개선
3. 금지 표현 필터 강화
4. loading/error 상태 polish
5. Docker/README 정리
6. 데모 시나리오 작성

---

## 4. 오늘의 바이브 코딩 추천 티켓

첫 구현은 아래 순서가 가장 덜 막히고, 화면까지 빨리 보입니다.

1. `packages/signal-core`에 공통 타입/스키마 추가
2. `services/agent-worker`에 DART 실제 수집 task 추가
3. `services/agent-worker`에 DART normalize/analyze task 추가
4. `services/agent-worker`에 SourceResult 저장과 Debate Aggregation 추가
5. `services/main-server`에 `/api/signals/run/{stockCode}` worker 호출과 결과 조회 추가
6. `web`에 Watchlist Dashboard 카드 UI 추가
7. Signal Journal form을 카드 하단에 붙이기

---

## 5. Definition of Done

- [ ] 사용자가 종목을 보고 분석 실행을 누를 수 있다.
- [ ] 수집 결과는 `raw_evidence`로 분리된다.
- [ ] 분석 결과는 `source_results`로 분리된다.
- [ ] 세 source의 방향성이 표시된다.
- [ ] 긍정 근거와 주의 근거가 분리된다.
- [ ] `needs_review`가 표시된다.
- [ ] Signal Journal을 작성하고 다시 볼 수 있다.
- [ ] 투자 추천으로 오해될 문구가 없다.
- [ ] 외부 API 실패 시에도 데모가 죽지 않는다.
