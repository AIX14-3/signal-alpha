# L1 정형 재무 수집 스펙 (DART `fnlttSinglAcntAll`)

> 팀장님(DART 파트) ↔ 본인 협업 스펙. `DART_LangChain_데이터준비_계획.md`의 **L1 정형 재무**를 실제 착수 가능한 수준으로 상세화한다.
> 연결 문서: `DART_분석_아이디어_마스터.md`(★4 정량 아이디어) · `협업안_검색트렌드_에이전트.md`(공통 신호 스키마).

---

## 0. 목적 / 왜 필요한가

현재 `services/agent-worker/app/analyzers/dart/financials.py`는 **공시 본문 텍스트를 정규식**으로 긁어 매출·영업이익·순이익만 뽑는다(⚠️ 부분 구현). 한계:
- 본문 표현에 의존 → 누락·오추출, 계정 커버리지 협소(YoY/QoQ·부채비율·CAPEX 불가).
- 시계열·표준계정이 없어 ★4 정량 아이디어 대부분이 시작 불가.

**L1 = 텍스트 추출을 OpenDART 정형 재무 API(`fnlttSinglAcntAll`)로 대체**하여, **표준계정 기반 다기간 재무 시계열**을 적재한다. 이게 ★4(YoY/QoQ·회전율·부채비율·CAPEX) 및 이후 RAG/thesis의 정량 토대다.

**범위 경계**
- L1(이 문서) = **수집·적재**(원천 정형 재무 → `dart_financial_facts` 테이블).
- ★4 파생지표·신호 emit은 **분석 단계**(별도 섹션 §7에서 계약만 정의, 구현은 후속).

---

## 1. 데이터 소스 — OpenDART `fnlttSinglAcntAll`

"단일회사 전체 재무제표" API. 한 번 호출로 BS/IS/CIS/CF/SCE 전 계정을 표준계정ID와 함께 반환.

**엔드포인트**
```
GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json
```

**요청 파라미터**
| 파라미터 | 의미 | 값 |
|---|---|---|
| `crtfc_key` | API 키 | 기존 `DART_API_KEY`(config) 재사용 |
| `corp_code` | 고유번호(8자리) | `dart_corp_codes`에서 조회 |
| `bsns_year` | 사업연도 | 4자리(예: 2025) |
| `reprt_code` | 보고서 종류 | `11013`=1분기 · `11012`=반기 · `11014`=3분기 · `11011`=사업보고서(연간) |
| `fs_div` | 재무제표 구분 | `CFS`=연결 · `OFS`=별도 |

**응답 주요 필드(계정 단위 리스트)**
| 필드 | 의미 |
|---|---|
| `rcept_no` | 접수번호(공시 식별, 정정 추적 키) |
| `sj_div` / `sj_nm` | 재무제표 구분(BS 재무상태표 / IS 손익 / CIS 포괄손익 / CF 현금흐름 / SCE 자본변동) |
| `account_id` | **표준계정ID**(예: `ifrs-full_Revenue`) — 정형화의 핵심 |
| `account_nm` | 계정명(예: "매출액") |
| `thstrm_amount` | 당기 금액 |
| `frmtrm_amount` | 전기 금액 |
| `bfefrmtrm_amount` | 전전기 금액 |
| `thstrm_dt` 등 | 각 기간 라벨/일자 |
| `currency` | 통화(보통 KRW) |
| `ord` | 표시 순서 |

> 한 번 호출에 당기/전기/전전기 3기간이 들어오지만, **연속 시계열은 `bsns_year`를 바꿔 다회 호출**해 채운다.
> `fnlttSinglAcnt`(주요계정)도 있으나, 커버리지를 위해 **All 버전 사용**.

---

## 2. 표준계정 매핑 (팀장님 소유 산출물)

`account_id` → **정규 metric 키**로 매핑하는 사전. ★4 파생의 입력이 된다. (회사마다 표시계정이 달라도 `account_id`로 표준화)

| 정규 metric | 대표 `account_id` | 재무제표 |
|---|---|---|
| `revenue`(매출) | `ifrs-full_Revenue` | IS |
| `operating_income`(영업이익) | `dart_OperatingIncomeLoss` | IS |
| `net_income`(당기순이익) | `ifrs-full_ProfitLoss` | IS/CIS |
| `total_assets`(자산총계) | `ifrs-full_Assets` | BS |
| `total_liabilities`(부채총계) | `ifrs-full_Liabilities` | BS |
| `total_equity`(자본총계) | `ifrs-full_Equity` | BS |
| `inventories`(재고자산) | `ifrs-full_Inventories` | BS |
| `trade_receivables`(매출채권) | `ifrs-full_TradeAndOtherCurrentReceivables` | BS |
| `operating_cash_flow`(영업현금흐름) | `ifrs-full_CashFlowsFromUsedInOperatingActivities` | CF |
| `capex`(유형자산취득) | `ifrs-full_PurchaseOfPropertyPlantAndEquipment...` | CF |
| `interest_expense`(이자비용) | `dart_InterestExpense`(회사별 상이) | IS/주석 |

> ⚠️ `account_id`가 비어있는 비표준 항목은 `account_nm` 폴백 규칙을 둔다. **이 매핑 사전의 범위·표기 확정이 팀장님과 합의할 1순위 항목.**

---

## 3. 적재 스키마 (본인 소유)

신규 테이블 제안. `database/migrations/<다음 가용 번호>_dart_financial_facts.sql`.
(DART의 `dart_corp_codes` 등과 동일 컨벤션: BIGSERIAL PK, created/updated_at, 인덱스)

```sql
CREATE TABLE dart_financial_facts (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT REFERENCES stocks(id),
    corp_code VARCHAR(20) NOT NULL,
    rcept_no VARCHAR(30) NOT NULL,          -- 정정 추적
    bsns_year SMALLINT NOT NULL,
    reprt_code VARCHAR(5) NOT NULL,         -- 11011/11012/11013/11014
    fs_div VARCHAR(3) NOT NULL,             -- CFS / OFS
    sj_div VARCHAR(5) NOT NULL,             -- BS/IS/CIS/CF/SCE
    account_id VARCHAR(100),                -- 표준계정ID (없을 수 있음)
    account_nm VARCHAR(200) NOT NULL,
    amount_krw NUMERIC(24,0),               -- 원 단위 정규화 금액
    amount_raw TEXT,                        -- 원본 문자열(검증용)
    currency VARCHAR(10) NOT NULL DEFAULT 'KRW',
    period_label VARCHAR(10) NOT NULL,      -- 2025FY / 2025Q3 등
    fiscal_period VARCHAR(10),              -- 누적/단일 구분 등
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 멱등 자연키: 같은 (회사,연도,보고서,재무제표구분,계정) 재수집 시 갱신
    CONSTRAINT uq_dart_fin_fact UNIQUE (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id)
);

CREATE INDEX idx_dart_fin_facts_stock ON dart_financial_facts (stock_id) WHERE stock_id IS NOT NULL;
CREATE INDEX idx_dart_fin_facts_corp_year ON dart_financial_facts (corp_code, bsns_year);
CREATE INDEX idx_dart_fin_facts_account ON dart_financial_facts (account_id);
```

> ORM은 `packages/data-access`의 `DartRepository` 패턴으로 `upsert_financial_facts(...)`(ON CONFLICT 멱등) 추가. (SEC `SecFilingRepository`와 동일 방식)

---

## 4. 수집 워크플로우

```
[대상 유니버스] (dart_corp_codes / 설정)
        ▼
for ticker → corp_code:
  for bsns_year in [최근 N년]:
    for reprt_code in [11011,11012,11013,11014]:
      fnlttSinglAcntAll(CFS)  ── 실패/빈값 ─▶ fnlttSinglAcntAll(OFS) 폴백
        ▼
   계정 파싱 → 단위 정규화(원) → upsert dart_financial_facts (rcept_no 최신 우선)
        ▼
[분석 단계 §7]  표준계정 매핑 → ★4 파생지표 → 공통 신호 스키마 emit → 백테스트
```

- **rate limit**: OpenDART 분당 호출 제한 → 기존 `DART_*` 백오프/간격 설정 재사용, 호출 캐싱.
- **증분 수집**: 이미 적재된 (corp,year,reprt,fs) 스킵 + 최신 정정만 갱신.

---

## 5. 엣지 케이스 (반드시 처리)

| 케이스 | 처리 |
|---|---|
| **분기 누적 vs 단일분기** | DART 분기보고서는 **누적**(반기=상반기 누적, 3Q=3분기 누적). 단일분기 필요 시 인접 누적치 차분. `fiscal_period`로 구분 보존 |
| **연결(CFS) vs 별도(OFS)** | CFS 우선, 없으면 OFS 폴백. `fs_div` 보존 |
| **정정공시** | 같은 (회사,연도,보고서)에 `rcept_no`가 갱신됨 → **최신 rcept_no 우선** upsert |
| **단위** | 금액은 원(KRW). `amount_krw` 정규화 + `currency` 보존(해외 ADR 대비). 기존 `financials.py`는 백만원 → 분석 단계에서 통일 |
| **비표준 계정** | `account_id` 빈 값 → `account_nm` 기반 폴백 매핑(§2) |
| **결측/0/음수** | 적재는 그대로, 비율 계산은 분모 0/음수 가드(예: PSR `market-data/valuation.py` 선례) |

---

## 6. 분담 & 마일스톤

> 담당: 🧑‍💼=팀장님 · 🙋=본인 · 👥=공동

| 작업 | 담당 | DoD |
|---|---|---|
| `fnlttSinglAcntAll` 수집기(`collectors/dart/financials_api.py`) | 🧑‍💼 | 30종목 ⨯ 최근 8분기 호출·파싱 성공 |
| **표준계정 매핑 사전**(§2) | 🧑‍💼 | 정규 metric 11종 매핑 확정 |
| `dart_financial_facts` 마이그레이션 + ORM 리포지토리 | 🙋 | 멱등 upsert·조회 + 실 DB 통과 |
| 적재 배선(수집기→리포지토리, 증분/정정) | 👥 | 30종목 적재, 재수집 시 행 수 불변(멱등) |
| ★4 파생지표 + 신호 emit(§7) | 🧑‍💼 | YoY/부채비율 등 공통 스키마 emit |
| 백테스트 lift(채택 게이트) | 🙋 | ★4 신호 lift 리포트 |

**합의 1순위(착수 전):** ① 표준계정 매핑 범위 ② CFS/OFS 우선순위 ③ 단일분기화 여부 ④ 신호 emit 단위.

---

## 7. 분석/신호 계약 (L1 출력 → 공통 스키마)

★4 파생지표는 `dart_financial_facts`를 입력으로 계산해 **공통 신호 스키마**(`협업안_검색트렌드_에이전트.md` §8)로 emit한다.

| 파생지표(★4) | 계산 |
|---|---|
| 매출/영업이익 YoY·QoQ | 동일 reprt 전년동기 대비 / 누적 차분 |
| 부채비율 | `total_liabilities / total_equity` |
| 재고·매출채권 회전율 | `revenue / 평균(inventories | trade_receivables)` |
| 이자보상배율 | `operating_income / interest_expense` |
| CAPEX 추세 | `capex` 시계열 기울기 — ★크로스 NPS capex |
| 이익의 질 | `operating_cash_flow − net_income` 괴리(발생액) |

emit 예(공통 스키마):
```jsonc
{ "source": "dart_financial", "ticker": "005930", "ts": "2026-03-15",
  "direction": "positive", "magnitude": 1.8, "confidence": 0.8,
  "evidence_ref": ["rcept_no:20260315000123"],
  "meta": { "metric": "revenue_yoy", "value": 0.23, "fs_div": "CFS", "period": "2025FY" } }
```
→ feature store 정렬 → **백테스트 lift로 채택 결정**(전 소스 공통 원칙).

---

## 8. 다음 액션
1. 팀장님과 **표준계정 매핑 사전(§2)** + **합의 1순위 4항목** 확정.
2. 🙋 `dart_financial_facts` 마이그레이션 + ORM 리포지토리 PR.
3. 🧑‍💼 `fnlttSinglAcntAll` 수집기 스파이크(1종목 1연도 파싱) → 30종목 확장.
4. 적재 → ★4 파생 → 백테스트 순으로 단계 상승.
