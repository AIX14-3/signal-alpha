# Signal Alpha 테스트 가이드

> 대상: 팀 전체(DART·Report·Hiring·Patent·DataLab + 최종 aggregator). 목적: 각 agent/code를 단위·통합·
> E2E로 어떻게 검증하는지 + 최종 aggregator 5방식 비교(bake-off)와 백테스팅 검증을 한 곳에 정리.

## 0. 테스트 3층 + 검증 1축 (한눈에)

| 층 | 무엇 | DB/LLM/API | 빠르기 | 도구 |
|---|---|---|---|---|
| **단위(unit)** | 순수 로직 1개(analyzer rule, per_source, source_hash) | 전부 mock/차단 | 빠름 | `IsolatedAsyncioTestCase` + Fake |
| **통합(integration)** | loader+DB, task handler | DB는 fixture/Fake, 외부 mock | 중간 | Fake repo/persistence |
| **E2E** | 큐 전체(enqueue→claim→run→final_signals) | 실DB(docker) | 느림 | QueueTaskRunner |
| **백테스팅(검증)** | 과거 시점 사후검증(별도 축, 단위테스트 아님) | point-in-time 실데이터 | — | 백테스트 하니스 |

**불변 규칙(모든 테스트가 지켜야 할 계약):**
- `SourceResult.score`는 **[-1,+1]** (−1 약세·0 중립·+1 강세). 벗어나면 `unknown` 강등 — 이걸 테스트로 단언.
- DB 적재는 0~100 (`_to_100 = (score+1)*50`). 경계(−1→0, 0→50, +1→100) 단언.
- Collector는 외부 API 호출 O, **LLM 호출 X**. Analyzer는 외부 API 호출 X(정규화된 행만 읽음).
- 사용자 노출 문구에 투자추천/`confidence` 단어 금지 → `signal-core` safety 테스트로 검증.

## 1. 실행법 (공통)

```powershell
uv sync --all-packages --group dev          # 1회 준비
# 서비스별 순차 실행(병렬 X — PYTHONPATH 충돌)
cd services/agent-worker   && uv run pytest -v --tb=short
cd ../main-server          && uv run pytest -v
cd ../../packages/data-access && uv run pytest -v
cd ../signal-core          && uv run pytest
cd ../market-data          && uv run pytest
# 특정 파일/클래스/메서드
uv run pytest tests/analyzers/test_patent_analyzer.py::PatentAnalyzerTest::test_falling_filings_is_negative -v
```
**CI**(`.github/workflows/ci.yml`): ruff lint → 패키지별 pytest → 마이그레이션 스모크(Postgres+seed) → web(tsc/build/test). PR마다 자동.

## 2. agent/code별 테스트 (무엇을·어떻게·mock 경계)

| 컴포넌트 | 입력 | 출력 | 단위 테스트 포인트 | mock 경계 | 기존 파일 |
|---|---|---|---|---|---|
| **Hiring analyzer** | `RawEvidence(metadata={rows, as_of})` | `SourceResult` | 방향/점수범위, 계절보정, 직군 sector_demand, 소표본 플래그 | 없음(순수) | `analyzers/test_hiring_analyzer.py`, `test_hiring_job_functions.py` |
| **Patent analyzer** | `RawEvidence`(±`llm_features`) | `SourceResult` | 농축 유/무 점수차, activity 연속성, stale | 없음(순수) | `analyzers/test_patent_analyzer.py` |
| **DataLab analyzer** | `RawEvidence`(rows, polarity_source) | `SourceResult` | 양방향(momentum±), spike, polarity_source="llm"일 때만 llm_model | 없음(순수) | `analyzers/test_datalab_analyzer.py`, `test_datalab_doublecount.py` |
| **DART agent** | `SourceAgentInput`(events) | `SourceAgentOutput` | **LLM=None이면 rule fallback**, LLM 에러→fallback+`llm_error` | `DartLlmAnalyzer` mock | `test_dart_llm_analysis.py`, `test_rule_source_agent.py` |
| **Report agent** | `SourceAgentInput`+retriever | `SourceAgentOutput` | retriever/LLM mock, 근거 희박→`needs_review`, timeout/JSON파싱 실패 fallback | retriever·`LlmClient` mock | `test_report_*.py` |
| **Collector**(4종) | 외부 클라이언트 응답 | raw_documents+detail+queue | source_hash 정규화, **중복→skip**, run status(success/partial/failed), 트랜잭션 롤백 | `FakeClient`+`FakeConnection` | `collectors/test_*`, `test_collection_persistence.py`, `test_hash_utils.py` |
| **per_source.build_source_signal** | `SourceResult`+`AggregatorConfig` | `AlternativeSignal` | 단일소스 confidence식, positive/caution 분리, 플래그→한글, 범위위반→unknown | 없음(순수) | `analyzers/test_per_source_signal.py` |
| **persistence.save** | `AlternativeSignal` | analysis_results/agent_results/final_signals | 0~100 스케일, **run_key별 1행**(HIRING/PATENT/…), upsert | Fake repo 또는 실DB | `analyzers/test_alternative_persistence.py` |
| **최종 aggregator(D-1~D-5)** | 5개 `SourceResult` | 1개 consensus | **미구현** — §4 참고 | 방식별 상이 | (없음) |

**LLM 쓰는 agent 테스트 원칙(DART·Report·D-2/3/5)**: 실제 LLM 호출 금지. mock으로 ① 정상 ② None(비활성)→rule fallback ③ 에러/timeout/파싱실패→fallback 3경로를 반드시 검증. (재현성: temperature=0)

## 3. 통합 & E2E

- **통합(loader+DB)**: detail 테이블에 fixture 삽입 → `loader.load(stock_id, code, as_of)` → analyzer → per_source → persistence → `final_signals` 재조회 단언. (docker postgres)
- **통합(task handler)**: `AlternativeAnalyzeTaskHandler`에 Fake repo/persistence 주입 → **소스마다 final_signals 1행·run_key 구분·agent_results debate_method 키** 단언. Normalizer 핸들러는 다음 task 인큐 단언.
- **E2E(큐)**: `QueueTaskRunner` + `build_task_handlers(conn)`로 enqueue→claim→run→완료. task 생명주기(pending→success/dead_letter)·retry·여러 task 독립 처리 단언.
- **수동 진입점**: `POST /internal/tasks/{TASK_TYPE}/enqueue` → `/run`; 로컬은 `run_analyzers.py`. patent는 `ENRICH_PATENT`(LLM 농축) 거쳐 `ANALYZE_ALTERNATIVE`.

## 4. 최종 aggregator 5방식(D-1~D-5) 테스트 & bake-off

미구현이라 *지금 정해야 할 테스트 설계*:

1. **공통 계약 테스트(5방식 동일)**: 같은 입력셋(5 SourceResult)을 주고 각 방식이 ① 출력 [-1,+1] ② safety(투자추천/confidence 문구 없음) ③ 소스 실패 시 점수상한 정책(0/1/2/3실패=상한 없음/없음/40/미제공, 기획 §6.5) ④ fallback 보유 — 를 만족하는지.
2. **결정론 vs 비결정론 분리**:
   - D-1(룰): 결정론 → 고정 입력에 **고정 점수** 단언(`test_patent_analyzer`식).
   - D-2/D-3/D-5(LLM): 비결정론 → temperature=0 + **golden 입출력 저장**, 점수 자체보다 **구조/범위/근거 존재**를 단언(값 정확매칭 금지).
3. **bake-off 하니스(방식 비교)**: 합성 시나리오 세트(예: 전소스+ / R&D만+·수요− / 1소스만 / 상관소스 가짜합의)를 만들어 **5방식 출력을 표로 나란히** 산출. 정답 라벨 = "방향 유지 + 근거 확인"(주가 아님). 표본 작아 *통계적 우열 아님*을 문서에 명시.
4. **독립성 보정 ablation**(선택, 어느 방식 위에든): 클러스터 `{hiring,patent}`/`{datalab}` on/off로 alignment_rate 변화 단언(상관소스 가짜합의 HIGH→MEDIUM 강등). → 별도 실험으로 분리 가능.

## 5. 백테스팅 검증 (단위테스트와 다른 축)

- 목적: "과거 신호가 이후에 **방향 유지 + 근거 확인**됐나"의 사후검증. **주가는 보조 관찰값만**(수익률·승률 표현 금지).
- **누수 차단을 테스트로 강제**: snapshot 생성이 `as_of` *이후* 데이터를 절대 안 읽는지 단언(미래 데이터 누수 = 치명적 버그).
- 표본(3종목·stale·특허mock) 한계상 *통계 증명*이 아니라 *사례·기능 검증*임을 전제. 실데이터 확보가 선행조건.

## 6. 현재 공백 & 우선순위 (탐색 기준)

1. **E2E 부재** → HIRING/PATENT/DATALAB 각 1개 기본 경로(collector→…→final_signals) 추가.
2. **per_source 엣지** → confidence 페널티 조합·범위위반·실패소스 케이스 보강.
3. **LLM fallback** → DART/Report의 None/에러/timeout 3경로 명시 테스트.
4. **최종 aggregator** → §4 공통 계약 테스트를 5명이 같은 베이스로 작성(구현 전에 합의).
5. **coverage 리포트 없음** → `pytest-cov` CI 추가(중기).

## 7. 새 테스트 작성 템플릿 (기존 패턴 복붙용)

```python
# Analyzer (순수) — DB/LLM 없음
class XAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_positive(self):
        ev = [RawEvidence(source="X", stock_code="005930", metadata={"rows": [...], "as_of": "2025-06-15"})]
        r = await XAnalyzer(CONFIG).analyze("005930", ev)
        self.assertEqual(r.direction, "positive")
        self.assertTrue(-1.0 <= r.score <= 1.0)

# Collector — FakeClient + FakeConnection, 중복 skip 단언
# Repository / aggregator method — FakeConnection.calls 로 SQL 검증, 0~100 스케일 단언
```
(전체 Fake 예시는 `services/agent-worker/tests/price_collector/fakes.py`, `test_collection_persistence.py` 참고)
