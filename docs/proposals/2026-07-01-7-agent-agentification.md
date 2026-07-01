# 제안: 서브 에이전트 7개 AI 에이전트화 (단방향 파이프라인 → 양방향 멀티에이전트)

> 작성 2026-07-01 · 담당(대체데이터) 이슬 · 상태: 팀 리뷰 요청
> 근거: 코드 실사 + Anthropic "Building effective agents" + TradingAgents(멀티에이전트) + 우리 ML 실험 기록

---

## TL;DR — 팀 의사결정 요약

- **문제**: 현재 6개 소스(DART·리포트·특허·채용·DataLab·주가)는 큐 위 **단방향 결정론 ETL**이다. 점수 산정 경로에 LLM 추론이 없고, 에이전트 간 피드백이 없어 "에이전트라 하기 어렵다".
- **제안**: 6 소스 서브에이전트 + 1 집계 오케스트레이터 = **7 에이전트화**. 단방향을 깨는 핵심은 **집계기가 소스에게 되묻는 양방향 피드백 루프**.
- **비용 우려 대응(중요)**: aggregator를 **맨 위로 올리지 않는다.** 지금처럼 **밑단 sink로 유지**하고, 불일치/근거부족 케이스(~10~20%)에만 **조건부 되묻기** 엣지 하나를 추가한다. 평소 비용은 현행과 동일.
- **불변식**: **숫자(방향/점수)는 결정론(규칙+메타러너)이 끝까지 소유.** LLM은 근거·판단·되묻기·`needs_review`만. 매수/매도·`confidence` 회피, "흔적 탐지" 유지.
- **임베딩 2종 포함**: 리포트 RAG(원문 이미 저장 → **재수집 0, 재처리만**) + 에피소드 메모리(과거 유사상황 회상). 단 pgvector·`report_chunks`가 전부 드롭돼 **신규 마이그레이션 필수**.
- **ML**: ML은 에이전트가 호출하는 **결정론 도구**(융합=메타러너). 넣을 곳은 **DataLab 매그니튜드 + aggregator 융합 + (선택)채용 나우캐스팅**뿐. 소스 단독 방향 ML은 실험으로 기각됨(재시도 금지).
- **로드맵**: Stage 0→3 게이트 기반. 풀 자율 ReAct+debate는 백테스트 lift·비결정성 예산 통과 후.

---

## 1. 진단 (코드 근거)

현재 6개 소스는 `processing_queue` 위 **단방향·결정론 규칙/수치 ETL**이다.
- 고정 체인(`drain_daemon.py` `DRAIN_ORDER`): COLLECT→NORMALIZE→ANALYZE→…→AGGREGATE→SYNTHESIZE→PUBLISH. 각 스테이지는 다음을 큐에 넣기만 함.
- 점수 경로에 **LLM 없음**. PATENT/PRICE는 규칙 + `graded()=weight*tanh(v/scale)`. DART/HIRING/DATALAB은 Phase0라 판정 안 하고 피처만.
- **에이전트 간 피드백 없음**. 집계기(`aggregation/tasks.py`)는 소스를 호출하지 않고 DB 적재 결과(JSONB 계약)만 fan-in해 산술평균.
- 생성형 LLM은 끝단 `SYNTHESIZE` 하나뿐(숫자 못 바꾸고 서술만).

## 2. "에이전트"의 정의와 결핍 2가지

에이전트(Anthropic) = **LLM이 도구를 스스로 골라 환경 피드백에 따라 계획·실행하고 자기 프로세스를 통제**. 현재 결핍 2가지:
1. **소스별 자율성**: 무엇을·얼마나 깊이 조사할지 스스로 도구 선택(비용 게이트), 자기비판(reflection), 숫자가 아닌 **근거+판단+출처** 산출.
2. **양방향 피드백**(단방향을 깨는 지점): 집계기가 소스 불일치/근거부족 시 특정 소스에 **집중 질문으로 되묻기**(bounded), 필요 시 긍정/주의 근거 **debate**.

**불변식(제품 규율 보존)**: 숫자는 결정론 소유 · 매수/매도·confidence 회피 · 모든 루프 상한+비용게이트 · LLM 실패 시 결정론 폴백 · LangGraph는 게이트 통과 시에만.

## 3. 목표 아키텍처: 7 에이전트

### 3.1 소스 서브에이전트 6개 — 자율성 티어로 차등

| 티어 | 소스 | 형태 | LLM 역할 | 도구 예시 |
|---|---|---|---|---|
| **A 풀 에이전트**(ReAct+reflection) | 리포트, DataLab | 다단계·동적 | 재해석/원인분류, 근거검색 | `search_report_chunks`(RAG), `reinterpret_valuation`, `spike_gate`, `classify_cause` |
| **B 씬 에이전트**(게이트 뒤 단발) | 특허, 채용 | 함수호출 1~2회 | significance/skill 판단 | `fetch_fulltext`, `classify_significance`, `ocr_extract_skills` |
| **C 결정론 에이전트**(계약만) | 주가, DART | 규칙 그대로 | 없음 | `compute_indicators` |

공통 계약 = 기존 `app/agents/base.py` `SourceAnalysisAgent`(`SourceAgentInput→SourceAgentOutput`). 티어 C는 `rule_source_agent.py`(규칙 래핑, 점수 불변). 티어 A/B는 도구 루프 추가:
`observe → choose_tool → act → observe …(상한) → reflect → emit`. **점수는 루프 안 규칙 함수가 계산, LLM은 근거/판단 보조만.**

### 3.2 집계 오케스트레이터 (7번째) — 양방향의 핵심

`AggregateSignalTaskHandler`(순수 fan-in)를 오케스트레이터로 승격:
1. **assemble** 6 SourceAgentOutput 수집(현행 재사용) → 2. **detect** 불일치/커버리지 얇음 감지(결정론 트리거) → 3. **re-query** 문제 소스에 되묻기(max 1~2라운드) → 4. **debate**(옵션·게이트) 긍정/주의 근거 → 5. **synthesize** 근거·source_agreement·needs_review → 6. **number** headline은 메타러너가 결정론 산출.

**⚠️ 오케스트레이터 ≠ 맨 위**: aggregator는 **밑단 sink로 유지**. 수집·스케줄은 계속 bottom-up 자율(오케스트레이터가 수집 지휘 안 함). 되묻기는 **분석 계층에만**(이미 수집된 원자료 재해석=재수집 아님), **조건부·희소**(~10~20%). 평소 80~90%는 현행과 동일 비용. → 단방향을 깨면서 "밑단 aggregator" 비용 결정을 보존.

## 4. 임베딩 2종

> 검색은 항상 쿼리 벡터가 필요하지만, **쿼리는 사람 키워드가 아니라 시스템 자동생성**이다. RAG=에이전트가 만든 분석 하위질문, 메모리=현재 상황 벡터 자체. 의미 유사도가 필요할 때만 임베딩을 쓴다.

**A. 에피소드 메모리(과거 유사상황 회상)**: 발행 신호 1건=1 에피소드(소스 발화·방향·근거·성패)를 임베딩 저장 → 새 상황과 유사한 과거를 reflection에 주입. reflection이 모델 사전지식이 아니라 **우리 이력**에 근거. 신규 `signal_episodes` 테이블 + pgvector. 위험=성패 라벨 오주입 시 미래정보 누수(회상은 참고만, 숫자 반영 금지).

**B. 근거검색 RAG(리포트/공시 청크)** — DB·수집 영향:

| 소스 | 원문 DB 저장 | RAG 재도입 |
|---|---|---|
| 리포트 | ✅ `report_raw_details.extracted_text` + PDF(GCS) 보존 | **재수집 불필요 — 재처리만** |
| 특허 | 🔸 제목 O, 초록 조건부, 청구항 ✗ | 초록=재처리, 청구항=재수집 |
| DART | ✗ 본문 미영속 | 재수집 필요(팀 영역) |

pgvector·`report_chunks`는 **전부 드롭 상태** → 신규 마이그레이션(`CREATE EXTENSION vector` + `report_chunks(embedding)`) 필수(거버넌스 `database/README.md §3/§4` 준수). 가장 크게 손대는 곳: ①DB 스키마 ②백필(저장 텍스트 청킹+임베딩; `parsers/chunker.py` 재활용) ③retriever 재작성. **집계 숫자 경로는 불변**. **v1은 리포트 RAG만(재수집 0), 특허 청구항/DART는 후속.**

## 5. 모델 라우팅 (per-task) & 비용

**원칙: 비싼 모델을 대량·쉬운 작업에 쓰지 않는다.** ($/1M in-out, 2026)

| 모델 | 단가 | 배정 |
|---|---|---|
| Gemini 2.5 Flash-Lite | $0.10 / $0.40 | 티어 B 대량(특허/채용/DataLab 추출), SYNTHESIZE |
| Claude Haiku 4.5 | $1 / $5 | 티어 A 후보 |
| Gemini 3.5 Flash | $1.50 / $9 | 티어 A 판단(리포트/DataLab cause) |
| Gemini 3.1 Pro / Claude Sonnet 4.6 | $2/$12 · $3/$15 | 오케스트레이터 debate/judge(희소) |
| text-embedding-005 | $0.006/1M | RAG·메모리(한국어 이슈 시 Gemini Embedding 2/Cohere embed-v4) |

**종목당 비용(혼합 라우팅)**: 현재 ~$0.0014 → 씬 ~$0.03 → 풀자율 ~$0.10(배치/캐시 시 ~$0.04). 200종목/일 기준 월 ~$8 → ~$180 → ~$600. **비용을 가르는 건 호출 수보다 "어느 태스크에 고급 모델을 허용하느냐"** + 게이트/티어링/배치. 코드에 이미 `GEMINI_MODEL` env·provider 스위치 존재 → 라우팅은 설정 확장.

## 6. 비결정성 관리

발생원=LLM 샘플링·동적 도구선택·되묻기 루프·검색 드리프트. 최악=headline 방향 뒤집힘 → **숫자를 결정론 소유하게 해서 원천 차단.** 측정="비결정성 예산"(고정 50종목 5회 반복 → 방향뒤집힘 0% 필수·needs_review 뒤집힘<5%·score σ<2). 완화=temp0+구조화 JSON·입력해시 캐시·골든/리플레이 테스트.

## 7. ML 통합 (실험 결과 반영)

ML=피처→숫자 결정론 두뇌(=§2 불변식의 숫자 소유자), 에이전트가 **도구로 호출**(`fuse_sources`=메타러너, 이미 존재).

| 소스 | 단독 방향 ML | ML | 통합 |
|---|---|---|---|
| DataLab | ❌기각 | ✅**매그니튜드**(변동성/거래량=유일 검증신호) | `predict_magnitude`+spike-gate |
| 특허 | ❌기각 | 피처 기여; 매그니튜드는 within-firm 붕괴="정적특성"(트레이더블 아님) | significance 추출→fusion |
| 채용 | ❌기각 | 매출 나우캐스팅만 유망(94종목 생존, 117 확대 미생존=marginal) | skill/duty 추출→fusion |
| DART/리포트/주가 | (팀) | 재무·밸류에이션 팩터, 주가=타깃 | 피처/라벨 |
| **aggregator** | — | ✅✅**융합=ML 본진**(`meta_learner.py`, 이미 존재) | `fuse_sources` 도구 |

**결론**: ①소스 단독 방향 ML 금지(전부 기각) ②ML 본진=aggregator 융합(강화만) ③DataLab만 소스-내 매그니튜드. **양방향 되묻기가 곧 ML 입력(피처) 개선** — 에이전트화와 ML 상호강화. 학습/평가는 오프라인 하니스, 에이전트는 추론만.

**미시도 프런티어(하니스 선검증 후 편입)**: 특허 텍스트 임베딩 피처(v1 임베딩 인프라 이중용도; within-firm+embargo 설계 필수) · 특허 매그니튜드 재설계(embargo·특허가치 피처·소형 유니버스) · 매출 나우캐스팅 확대. ⛔ 주가 라벨 재테스트는 완료(반복 금지), 새 라벨은 매출.

## 8. 로드맵 (게이트 기반)

- **Stage 0** — 계약 통일 + 임베딩 인프라(DB 마이그레이션). LLM 판단 증가 0.
- **Stage 1** — 소스 자율성(씬, DataLab cause 병합→특허·채용 일반화) + 리포트 RAG 백필.
- **Stage 2** — 오케스트레이터 양방향(조건부 되묻기) + 에피소드 메모리. **단방향이 깨지는 지점.**
- **Stage 3** — 풀 자율 ReAct + debate. **LangGraph는 "진짜 분기≥2 + 백테스트 lift 입증 + 비결정성 예산 통과"일 때만.**

## 9. 재사용 자산 & 스코프

재사용: `agents/base.py`·`rule_source_agent.py`(계약), `sa-datalab-cause`의 datalab 에이전트(spike-gate 선례), `aggregation/tasks.py`(개조), `gemini_client.py`(LLM), `queue/*`(드레인), `parsers/chunker.py`(청킹).

**스코프**: 구현은 대체데이터(HIRING/PATENT/DATALAB)+aggregator. **DART·리포트·주가는 팀 영역** — 계약 인터페이스만 정의, 구현은 협의. 실험/하니스는 신호 전 머지 보류.

## 부록: 참조
- 팀 규율: `docs/spec/cross-layer-orchestration-and-risks.md`(3단 사다리·LangGraph 게이트), `docs/spec/source-agent-contract.md`
- RAG 제거 현황: `docs/spec/report-rag-current-state.md`, `database/README.md:173`
- 제품 철학: `docs/archive/project-context.md`(§9 흔적탐지 / §10 긍정·주의 근거·confidence 회피)
