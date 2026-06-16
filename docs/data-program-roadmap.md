# Signal α — 데이터 프로그램 전체 로드맵

> 개별 계획/스펙 문서를 묶는 **상위 로드맵 & 인덱스**. "무엇을, 어떤 순서로, 누가, 어떤 계약으로" 만드는지의 한 장짜리 지도.
> 세부는 각 문서로 링크(§9 문서 맵). 레포 실제 구현(`services/agent-worker/app/{collectors,analyzers}`, `database/migrations`) 기준으로 현황을 표기.

---

## 1. 비전 / 포지셔닝

**Signal α = 매수·매도 추천이 아니라, 여러 데이터 소스가 같은 방향을 가리키는지 교차검증해 근거를 보여주는 서비스.**
- 단일 소스로 단정하지 않는다. 공시·리포트·검색트렌드·수급·채용·특허를 **공통 신호로 환산**해 교차한다.
- 모든 신호는 **백테스트 lift로 채택**을 결정한다(주관 배제).
- LLM/에이전트 출력은 **가드레일 통과 + 인용(evidence)** 필수.

---

## 2. 전체 파이프라인

```
[수집 collectors] → [정규화·분석 analyzers] → [공통 신호 스키마 emit]
       → [feature store 정렬] → [백테스트 lift(심판)] → 채택
                                                    └→ [다단계 추론·thesis] (★7~8, LangGraph 게이트)
```
- 모든 소스는 **같은 신호 스키마**(§5)로 emit → 하나의 백테스트로 비교.
- LLM/LangChain은 파이프라인 **맨 끝(다단계 추론)** 에서만, 그것도 백테스트로 가치가 입증된 뒤 도입.

---

## 3. 데이터 소스 & 구현 현황

> 담당: 🙋=본인(데이터·스키마·백테스트) · 🧑‍💼=팀장님(DART) · 👤=트렌드 팀원 · 상태는 레포 코드 대조.

| 소스 | 모듈 | 상태 | 담당 | 비고 |
|---|---|---|---|---|
| 공시(국내) DART | `collectors/dart`,`analyzers/dart` | ⚠️ 부분(룰·LLM·텍스트재무) | 🧑‍💼 | 정형 재무(L1)로 심화 예정 |
| **공시(해외) SEC** | `collectors/sec` | 🟡 수집기 골격(PR #128) | 🙋 | EDGAR, 대상 유니버스 연결 완료 |
| 증권사 리포트 RAG | `collectors/report`,`report_chunks` | ⚠️ 부분(pgvector·BGE-M3) | 🙋 | 의존성 정리 필요 |
| 검색 트렌드(데이터랩) | `collectors/datalab`,`analyzers/datalab` | ⚠️ 부분 | 👤 | LangGraph attention 에이전트 |
| 수급·가격 | `collectors/price`,`analyzers/price` | ✅ 실시간(키움 REST) | 🙋 | 공매도/외국인/창구 hard data |
| 채용 | `collectors/hiring`,`analyzers/hiring` | ⚠️ 부분 | 🙋 | NPS·DART와 3중 교차 |
| 특허 | `collectors/patent`,`analyzers/patent` | ⚠️ 부분 | 🙋 | KIPRIS |
| 밸류에이션(PSR 등) | `packages/market-data` | ✅ 머지(#125) | 🙋 | PSR 파생 계산 |
| **CI(검증 인프라)** | `.github/workflows/ci.yml` | ✅ 머지(#123) | 🙋 | lint·test·build·마이그레이션 |

---

## 4. 데이터 레이어 (L1~L6)

`DART_LangChain_데이터준비_계획.md`의 레이어 모델 — LangChain 후보화의 전제.

| 레이어 | 내용 | 상태 |
|---|---|---|
| L1 정형 재무 | DART `fnlttSinglAcntAll` → `dart_financial_facts` | 📝 스펙 작성(PR #129) |
| L2 지분·내부자 | `majorstock`/`elestock` | ⬜ 예정 |
| L3 임직원 | 사업보고서 정형 | ⬜ 예정 |
| L4 비정형 corpus | 보고서 섹션 → `report_chunks` 임베딩 | ⚠️ RAG 인프라 일부 |
| L5 엔티티·관계 | corp_code·발주처 그래프 | ⬜ 예정 |
| L6 백테스트 패널 | 이벤트 ⨯ price forward return | ⬜ 예정 |

상세 스펙: L1 → `docs/spec/dart-l1-financials-spec.md`, **L2~L6 → `docs/spec/data-layers-l2-l10-spec.md`**.
토대 원칙(PIT·feature store·평가지표·DQ) + L1~L10 워크플로우 → `docs/spec/data-foundations-and-l1-l10-workflow.md`.
→ SEC(해외)도 동일 레이어 모델을 `source:"sec"`로 따른다(`해외공시_데이터수집_계획.md`).

**확장 비전(미구현, 방향 고정용)** — 상세는 위 L2~L10 스펙 Part B:
| 레이어 | 내용 | 기술 |
|---|---|---|
| L7 멀티스텝 추론·시나리오 | 멀티섹션 RAG → 구조화 thesis | LangGraph(게이트 §8) |
| L8 예측·ML | 공시 시퀀스·부실 조기경보·어닝 예측 | sklearn/XGBoost (LangChain 아님) |
| L9 인과추론(선택) | 이벤트 인과 검증·업종 클러스터 | DiD/통제군 |
| L10 자율 thesis·멀티모달 | 전 소스 통합 Bear/Base/Bull | 멀티에이전트+ML, 가드레일 필수 |

---

## 5. 공통 신호 스키마 (모두가 emit하는 계약)

`협업안_검색트렌드_에이전트.md` §8 + `DART_LangChain_데이터준비_계획.md` §3 통합. 적재 테이블은 `signal_events`/`final_signals`.

```jsonc
Signal {
  source: "dart" | "sec" | "trend" | "price" | "hiring" | "patent" | "report" | "dart_financial",
  ticker: "005930",
  ts:        "2026-03-15",     // 신호 시점(정렬 기준)
  direction: "positive|negative|neutral|mixed",
  magnitude: 1.8,              // z-score 등 표준화 강도
  confidence: 0.8,
  cause:     "catalyst|fomo|price_led|null",  // 트렌드 전용
  evidence_ref: ["rcept_no:...","accession:...","url:..."],  // 인용/감사추적
  meta:      { /* 소스별 부가 */ }
}
```
> 이 계약이 ① 멀티소스 교차 ② 백테스트 ③ (★7~8) 다단계 추론의 근거추적을 동시에 가능케 한다. **인터페이스부터 합의, 구현은 병렬.**

---

## 6. 팀 분담 (코드 합치지 않고 계약으로 결합)

| 영역 | 담당 |
|---|---|
| 수급·가격·SEC·리포트·채용·특허 수집 + **공통 스키마·feature store·백테스트(심판)** | 🙋 본인 (소유자) |
| DART 공시·정형 재무(L1~)·★4 정량·표준계정 매핑 | 🧑‍💼 팀장님 |
| 검색 트렌드 수집 + LangGraph attention 에이전트 | 👤 트렌드 팀원 |

접점은 둘뿐: **① 공통 신호 스키마 ② hard data read API.** 나머지는 병렬.

---

## 7. 단계별 로드맵 (전 소스 관통)

| Phase | 목표 | 현재 위치 |
|---|---|---|
| **0 토대** | 공통 신호 스키마 확정, corp_code/ticker·CIK 매핑, price 패널, **CI** | ✅ CI 완료, 🟡 스키마 합의 진행 |
| **1 정형 피드** | DART L1 재무, SEC 수집 적재, 수급 hard data | 🟡 SEC 골격·L1 스펙 |
| **2 비정형 corpus** | 보고서/10-K 섹션 → 임베딩(RAG) | ⚠️ 일부 |
| **3 신호+백테스트** | 전 소스 공통 스키마 emit → lift 검증 게이트 | ⬜ |
| **4 다단계 추론** | 멀티섹션 RAG·트렌드 에이전트·thesis → **LangChain/LangGraph PoC** | ⬜ (게이트 §8) |

> 문서 원칙: 처음부터 ★8/프레임워크로 가지 않는다. 정형 토대 → 신호 → 백테스트 입증 → 추론.

---

## 7-b. 교차연결 오케스트레이션 & 리스크 (별도 스펙)

- **L1~L10 교차연결 컨트롤 플레인**(LangGraph): 한 레이어 유입 → 연관 레이어 깨워 재수집·검색·검증·분석 루프. 예: L1 유입 → L3·L7·L10 재수집. (L7+ 영역, 게이트 통과 후)
- **리스크 레지스터 + 비중 판단 프레임**: 우려별 심각도×가능성 점수 + 처리 시점(지금/중기/L7+). 토대 단계 필수 항목 = PIT(R1)·정정(R2)·feature store(R3)·평가지표(R4).
- 상세: `docs/spec/cross-layer-orchestration-and-risks.md`

## 8. 의사결정 게이트

- **신호 채택 게이트**: 모든 신호는 백테스트 **lift**로만 채택/기각. 주관·복잡도로 채택 금지.
- **LangChain/LangGraph 도입 게이트**(모두 충족 시에만):
  - [ ] L1~L4 적재되어 멀티섹션 RAG + 정량 features 동시 조회 가능
  - [ ] 공통 스키마 `evidence_ref`로 근거추적 가능
  - [ ] 다단계 추론이 단일 신호보다 lift를 더한다는 근거
  - [ ] self-built 루프 유지보수 부담 → 이때 **LangGraph 우선**
  - 그 전까지는 `gemini_client` + function calling + 가드레일로 충분.

---

## 9. 문서 맵

**레포 내 (`docs/`)**
- 이 문서 — `docs/data-program-roadmap.md` (전체 로드맵/인덱스)
- `docs/spec/dart-l1-financials-spec.md` — L1 정형 재무 상세 (PR #129)
- `docs/spec/dart-collector-analyzer-spec.md` — DART 수집/분석 스펙
- `docs/spec/kiwoom-rest-spec.md`, `source-agent-contract.md`, `agent-worker-hiring.md` 등

**설계 문서 (현재 작성자 로컬 — 레포 `docs/`로 이전 권장)**
- `DART_분석_아이디어_마스터.md` — ★3~★8 아이디어 사다리
- `DART_LangChain_데이터준비_계획.md` — L1~L6 레이어·Phase·LangChain 게이트
- `해외공시_데이터수집_계획.md` — SEC EDGAR(해외 공시)
- `협업안_검색트렌드_에이전트.md` — 트렌드 LangGraph 에이전트 협업·신호 스키마

> 권장: 위 설계 문서를 레포 `docs/`로 옮기면 팀 전체가 링크로 추적 가능(이 로드맵이 인덱스 역할).

---

## 10. 불변 원칙 (전 작업 공통)

1. 모든 신호 → **공통 스키마 emit → 백테스트 lift로 채택**.
2. **계약(스키마+read API)부터 합의**, 구현은 병렬. 스키마 이중화·공용 인프라 우회 금지(과거 코드리뷰 함정).
3. LLM 출력은 **가드레일 + 인용** 필수. "매수·매도 추천 아님" 포지셔닝 유지.
4. **하드코딩 금지** — 대상/엔드포인트/키는 설정·DB. 의존성은 반드시 선언.
5. 마이그레이션은 `NNN_*.sql` 추가만(checksum·LF 규칙), 적용본 수정 금지.
6. 단계적 상승 — 정형 토대 → 신호 → 백테스트 → 다단계 추론/프레임워크.
