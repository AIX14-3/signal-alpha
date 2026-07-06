# 데이터 레이어 상세 스펙 — L2~L6 (착수) + L7~L10 (확장 비전)

> `data-program-roadmap.md`의 레이어 모델을 착수 가능한 수준으로 상세화. L1은 별도(`dart-l1-financials-spec.md`).
> **Part A(L2~L6)** = 지금 만드는 토대. **Part B(L7~L10)** = 미구현이지만 방향을 고정해 두는 확장 비전(마스터 문서 ★7~★8 대응).
> 연결: `DART_분석_아이디어_마스터.md` · `DART_LangChain_데이터준비_계획.md` · `협업안_검색트렌드_에이전트.md`.

---

## 0. 레이어 지도

```
[토대 — 착수]                                        [확장 — 비전(백테스트 입증 후)]
 L1 정형 재무 ─┐                                       L7 멀티스텝 추론·시나리오 (LangGraph)
 L2 지분·내부자 ├─► L6 백테스트 패널 ──(lift 채택)──►   L8 예측·ML
 L3 임직원     │                                       L9 인과추론 (bridge, 선택)
 L4 비정형 corpus(RAG)                                 L10 자율 thesis·멀티모달 (장기)
 L5 엔티티·관계 ┘
```
- L2~L6은 LangChain과 무관하게 각자 ★4~6 신호를 내어 즉시 가치를 낸다.
- L7~L10은 **L1~L6이 쌓이고 L6 백테스트로 가치가 입증된 뒤에만** 착수(도입 게이트 §B-0).

공통 규칙(전 레이어): 적재는 **자연키 멱등 upsert**, 신호는 **공통 스키마 emit**(`협업안` §8), 마이그레이션은 `NNN_*.sql` 추가, 하드코딩 금지·의존성 선언.

---

# Part A — 착수 대상 상세 (L2~L6)

각 레이어: 목적 / 소스·API / 적재 스키마(제안) / 워크플로우 / 엣지케이스 / 분담 / 신호 계약.

## L2 — 지분·내부자

**목적**: 5%+ 대량보유·임원/주요주주 소유 변동에서 내부자 신호(순매수/매도 추세) 도출. (마스터 ★4)

**소스(OpenDART)**
- `majorstock` (대량보유 상황보고): `repror`(보고자), `stkqy`(보유수), `stkrt`(보유비율), `stkqy_irds`/`stkrt_irds`(증감), `report_tp`, `rcept_no`
- `elestock` (임원·주요주주 소유보고): `repror`, `isu_exctv_ofcps`(직위), `sp_stock_lmp_cnt`(소유수), `sp_stock_lmp_irds_cnt`(증감)

**적재 스키마** `dart_ownership_events`
```sql
CREATE TABLE dart_ownership_events (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL,
    rcept_no VARCHAR(30) NOT NULL,
    report_date DATE NOT NULL,
    holder_name VARCHAR(200) NOT NULL,
    holder_type VARCHAR(20) NOT NULL,        -- major(5%) / executive / main_shareholder
    shares NUMERIC(20,0),
    ratio NUMERIC(8,4),                      -- 보유비율(%)
    shares_delta NUMERIC(20,0),              -- 증감
    ratio_delta NUMERIC(8,4),
    report_reason VARCHAR(100),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ownership_event UNIQUE (corp_code, rcept_no, holder_name, holder_type)
);
```
**워크플로우**: 유니버스 → `majorstock`/`elestock` 조회 → 보고자 정규화 → upsert → 추세 집계.
**엣지케이스**: 보고자명 표기 흔들림(엔티티 정규화 → L5 연계), 정정(rcept 최신), 공동보유 합산.
**분담**: 🧑‍💼 수집·정규화 / 🙋 스키마·ORM·신호 배선.
**신호 계약**: `source:"dart_ownership"`, direction = 순매수(+)/순매도(−), magnitude = 비율증감 z-score, evidence_ref = rcept_no.

## L3 — 임직원 현황

**목적**: 직원 수·근속·평균급여 추세 = 고용 모멘텀. NPS·채용공고와 **3중 교차**(마스터 F).

**소스(OpenDART)** `empSttus` (직원 현황, 사업보고서): `fo_bbm`(사업부문), `rgllbr_co`(정규직), `cnttk_co`(계약직), `sm`(합계), `avrg_cnwk_sdytrn`(평균근속), `jan_salary_am`(1인평균급여), `fyer_salary_totamt`(연간급여총액)

**적재 스키마** `dart_employee_stats`
```sql
CREATE TABLE dart_employee_stats (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL,
    bsns_year SMALLINT NOT NULL,
    reprt_code VARCHAR(5) NOT NULL,
    segment VARCHAR(100),                    -- 사업부문(fo_bbm)
    headcount INTEGER,                       -- 합계 인원(sm)
    regular_count INTEGER,
    contract_count INTEGER,
    avg_tenure_years NUMERIC(6,2),
    avg_salary_krw NUMERIC(20,0),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_employee_stats UNIQUE (corp_code, bsns_year, reprt_code, segment)
);
```
**엣지케이스**: 사업부문 분할/합산 행, 결측, 단위(원), 비교는 동일 reprt 기준.
**분담**: 🧑‍💼 수집 / 🙋 스키마·ORM / 👥 NPS·채용 교차 정의.
**신호 계약**: `source:"dart_employee"`, meta.metric = headcount_yoy, ★크로스 hiring/NPS.

## L4 — 비정형 corpus (RAG 토대)

**목적**: 사업/분기보고서·10-K 본문을 **섹션 단위**로 분해·임베딩해 RAG·톤분석·종단비교의 토대 마련(마스터 ★5~6).

**소스**: DART `document.xml`(수집됨) + SEC 10-K/10-Q. 섹션: 사업의 내용 / MD&A / 위험요인 / 주석 / 감사보고서.

**스키마**: `report_chunks`는 현재 Report 런타임에서 사용하지 않는 잔존 스키마이므로 L4에서 재사용하지 않는다. L4 착수 시 DART/SEC용 신규 `document_sections`/`document_chunks` 계열 스키마를 별도 마이그레이션으로 설계한다.
```sql
-- 예시: L4 전용 신규 스키마. 실제 컬럼/인덱스는 별도 구현 PR에서 확정한다.
CREATE TABLE document_sections (...);
CREATE TABLE document_chunks (...);
CREATE INDEX idx_document_chunks_section ON document_chunks (stock_id, section_type);
```
**워크플로우**: 원문 → 섹션 분해 → 청킹 → 임베딩 → L4 전용 chunk 테이블 적재 → top-k 검색(evidence_ref 부착).
**선결 과제**: `chunker.py`의 `langchain_text_splitters` **의존성 미선언/미설치 정리**(선언 or 자체 splitter) — L4 착수 전 필수.
**엣지케이스**: 섹션 경계 모호, 표/이미지, 다국어(BGE-M3 OK), 토큰 한도, 정정본 재임베딩.
**분담**: 🙋 섹션분해·임베딩 파이프라인 / 👥 섹션 사전 정의.
**신호 계약**: L4 자체는 신호보다 **retrieval 토대**. 톤변화(★5)는 분석 단계에서 emit.

## L5 — 엔티티·관계

**목적**: 기업·발주처·거래처를 정규화하고 거래/투자/계열 **관계 그래프**(마스터 ★6, NPS 공급망 교차). 비상장(OpenAI 등) 우회 추적의 토대(상장 상대방 edge).

**소스**: `dart_corp_codes`(국내) + SEC `cik_map`(해외) + 수주/투자공시 본문에서 발주처 LLM 추출.

**적재 스키마**
```sql
CREATE TABLE entities (
    id BIGSERIAL PRIMARY KEY,
    canonical_name VARCHAR(200) NOT NULL,
    corp_code VARCHAR(20),                   -- 국내(DART)
    cik VARCHAR(10),                         -- 해외(SEC)
    ticker VARCHAR(20),
    entity_type VARCHAR(20) NOT NULL,        -- listed / private / counterparty
    CONSTRAINT uq_entity UNIQUE (canonical_name)
);
CREATE TABLE entity_relations (
    id BIGSERIAL PRIMARY KEY,
    src_entity_id BIGINT NOT NULL REFERENCES entities(id),
    dst_entity_id BIGINT NOT NULL REFERENCES entities(id),
    relation_type VARCHAR(30) NOT NULL,      -- supply / order / affiliate / investment
    evidence_rcept VARCHAR(30),
    confidence NUMERIC(4,3),
    observed_at DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
**엣지케이스**: **엔티티 정규화(난이도 高)** — 표기 변형·약칭·영문/국문, 동명이인. confidence로 불확실성 보존.
**분담**: 👥 (정규화 규칙 합의), 🙋 스키마, 🧑‍💼 발주처 추출.
**신호 계약**: 관계 자체는 feature. "공급망 동반 신호"는 분석 단계 emit.

## L6 — 백테스트 패널 (채택의 심판)

**목적**: 모든 신호의 **lift를 forward return으로 검증**해 채택/기각(마스터 H ★6). 본인 소유, 전 소스 공통 게이트.

**소스**: `signal_events`(공통 신호) ⨯ `ohlcv_data`(price).

**적재 스키마** `event_study_panel`
```sql
CREATE TABLE event_study_panel (
    id BIGSERIAL PRIMARY KEY,
    signal_event_id BIGINT NOT NULL REFERENCES signal_events(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    event_date DATE NOT NULL,
    fwd_return_1d NUMERIC(10,6),
    fwd_return_5d NUMERIC(10,6),
    fwd_return_20d NUMERIC(10,6),
    abnormal_return_20d NUMERIC(10,6),       -- 시장/섹터 벤치 대비
    benchmark VARCHAR(20),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_panel UNIQUE (signal_event_id)
);
```
**방법론**: 이벤트 스터디 — 신호일 기준 forward window 수익률, 시장/섹터 벤치 대비 abnormal return, 신호 유형별 평균 lift·승률·유의성.
**엣지케이스**: look-ahead bias 차단(이벤트 시점 이후 데이터만), 생존편향, 휴장/상폐, 소표본 유의성.
**분담**: 🙋 (소유). 산출물 = 신호별 lift 리포트 → 채택 결정.
**신호 계약**: L6은 신호를 **소비/평가**(emit 아님). 결과가 다른 레이어의 채택을 좌우.

---

# Part B — 확장 비전 (미구현, L7~L10)

> 지금 구현하지 않는다. **방향을 고정**해 두어 L2~L6 설계가 이 방향과 어긋나지 않게 한다.

## B-0. 도입 게이트 (L7+ 공통 전제)
아래가 **모두** 충족될 때만 상위 레이어 착수:
- [ ] L1~L4 적재로 한 종목에 **정량 features + 멀티섹션 RAG** 동시 조회 가능
- [ ] 공통 스키마 `evidence_ref`로 근거추적
- [ ] **L6 백테스트에서 기반 신호의 lift가 입증**됨
- [ ] 다단계/예측이 단일 신호보다 lift를 더한다는 근거

## L7 — 멀티스텝 추론·시나리오 (LangGraph)
- **목표**(마스터 ★7): 자본조달 패턴→행동예측, 지배구조 시나리오, 숨은 catalyst **멀티섹션 RAG**→실적 선행 추정.
- **전제 데이터**: L4 corpus + L1 재무 + L5 관계 + L2 지분.
- **기술**: **LangGraph**(상태형 다단계: planner→retrieve→cross_check→synthesizer) + 가드레일 + 인용. (`협업안` 트렌드 에이전트와 동일 패턴 — 여기서 LangChain/LangGraph가 비로소 값을 함.)
- **출력**: 구조화 thesis 후보 → 공통 스키마 emit → L6 백테스트.
- **리스크**: 근거추적 없으면 환각 생성기. evidence_ref 필수.

## L8 — 예측·ML
- **목표**(마스터 ★8): 공시 시퀀스 전개 예측(무상감자→유증→관리종목), 부실/분식 조기경보, 어닝 서프라이즈 예측.
- **전제 데이터**: L1~L6의 feature store + 라벨(이벤트 결과).
- **기술**: 전통 ML(sklearn/XGBoost)·시계열·이상탐지. **LangChain 아님**(LLM은 feature 추출 보조에 한정).
- **게이트**: L6 백테스트로 모델 lift 검증, 과적합·누설 통제.

## L9 — 인과추론 (bridge, 선택)
- **목표**: 이벤트 스터디 고도화 — 어떤 공시 유형이 초과수익과 **인과적으로** 연결되는지 통제 검증, 동종업종 클러스터.
- **기술**: 인과추론(DiD/통제군), L6 패널 확장.
- L7/L8과 L10 사이 다리. 선택적.

## L10 — 자율 thesis·멀티모달 (장기 비전)
- **목표**(마스터 ★8 최고난도): 재무(발생액)+텍스트(감사)+공시패턴 **멀티모달 종합 부실예측**, DART+SEC+트렌드+수급 전 소스 통합 **자율 Bear/Base/Bull thesis** 자동 생성.
- **전제**: L7+L8+L9 성숙 + 전 소스 신호의 백테스트 누적.
- **기술**: 멀티에이전트 오케스트레이션(LangGraph) + ML 앙상블 + 강한 가드레일.
- **불변 제약**: "매수·매도 추천 아님" 포지셔닝 유지 — 최종 thesis 합성은 **사람이 소유하는 분석·백테스트 틀 안에서**, 가드레일·인용 필수(`협업안` §5 경계 원칙).
- **리스크**: 경계 붕괴·과신. 백테스트 lift 없이는 표시하지 않는다.

---

## 부록 — 신호 계약 일관성
L2~L10 전부 동일 `Signal{source,ticker,ts,direction,magnitude,confidence,cause?,evidence_ref,meta}`로 emit(`협업안` §8). source 값만 추가(`dart_ownership`/`dart_employee`/`dart_thesis` 등). 이 일관성이 멀티소스 교차·백테스트·근거추적을 동시에 가능케 한다.

## 다음 액션
1. L2·L3는 L1과 동일 패턴이라 합의 즉시 착수 가능(🧑‍💼 수집 + 🙋 스키마/ORM).
2. L4는 `chunker.py` 의존성 정리 선행.
3. L6은 `signal_events`가 쌓이기 시작하면 병행(🙋).
4. L7~L10은 **L6 lift 입증 게이트** 통과 후. 그 전엔 설계 방향 고정용으로만 유지.
