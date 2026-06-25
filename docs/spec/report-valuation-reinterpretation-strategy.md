# 리포트 밸류에이션 재해석 전략

최종 갱신일: 2026-06-25

## 목적

이 문서는 증권사 리포트를 단순 목표가 비교가 아니라 **밸류에이션 가정의 변화**로 재해석하기 위한 개발 방향을 정리합니다.

Signal Alpha는 투자 추천 서비스가 아닙니다. 이 전략의 출력은 매수, 매도, 보유 추천이 아니라 데이터 방향성, 근거, 소스 간 일치도, 추가 확인 필요 여부를 보여주는 사용자 판단 보조 정보입니다.

## 핵심 관점

리포트 분석의 중심을 목표주가 자체에서 아래 항목으로 옮깁니다.

- 적용 배수와 내재 배수
- 피어 그룹과 비교 기준
- 카테고리 또는 내러티브 변화
- 증권사 간 밸류에이션 가정의 분산
- DART, DataLab, Price 같은 다른 소스와의 교차 확인

목표주가는 결과값이고, 이 전략에서 더 중요한 원천 데이터는 “왜 그 목표주가가 나왔는가”입니다.

## 저작권 및 저장 원칙

리포트 원문은 사용자에게 노출하지 않습니다. 전략용 산출물은 원문 문장 복제가 아니라 구조화된 사실과 패러프레이즈된 해석입니다.

원칙:

- 공개 접근 가능한 리포트와 공개 페이지 기준으로 수집합니다.
- 유료, 로그인, 우회 접근이 필요한 자료는 수집 대상에서 제외합니다.
- 사용자-facing 응답에는 원문 PDF 또는 긴 verbatim 청크를 노출하지 않습니다.
- 밸류에이션 전략용 저장물은 구조화 fact 중심으로 둡니다.
- `rerating_thesis`는 원문 인용이 아니라 짧은 패러프레이즈로 저장합니다.
- 내부 RAG용 `report_chunks`는 근거 검색과 검증용으로만 사용하고, 장기적으로 보존 기간 또는 fact-only 모드를 검토합니다.

## 추출 대상

초기 MVP의 구조화 산출물은 다음 계약을 목표로 합니다.

```text
ReportValuationFact
```

| 필드 | 설명 |
| --- | --- |
| `raw_document_id` | `raw_documents.id` 참조 |
| `stock_id` | 종목 ID |
| `ticker` | 종목 코드 |
| `broker` | 증권사 |
| `analyst` | 애널리스트명 |
| `publish_date` | 발행일 |
| `target_price` | 목표가 원천 값 |
| `forward_eps_est` | 추정 EPS |
| `eps_fy` | EPS 기준 연도 |
| `methodology` | `PER`, `PBR`, `EV_EBITDA`, `SOTP`, `DCF`, `mixed`, `unknown` |
| `applied_multiple` | 리포트에 명시된 적용 배수 |
| `implied_multiple` | `target_price / forward_eps_est`로 계산한 내재 배수 |
| `peer_group` | 비교 기업 목록 |
| `category_tag` | 카테고리 또는 내러티브 태그 |
| `rerating_thesis` | 재평가 논리의 패러프레이즈 |
| `extraction_source` | `rules`, `llm`, `rules_fallback` 등 |
| `needs_review` | 수치 결측, 충돌, LLM 검증 실패 여부 |

## 수치와 LLM 책임 분리

수치 계산은 코드가 담당합니다.

- `implied_multiple = target_price / forward_eps_est`
- 증권사 간 내재 배수 분산
- 피어 배수 대비 갭
- Bear/Base/Bull 밴드의 수치 계산
- 결측, 0, 음수, 비정상 범위 검증

LLM은 해석과 분류만 담당합니다.

- valuation methodology 분류 보조
- peer group 후보 정리
- category tag 분류
- rerating thesis 패러프레이즈
- Bear/Base/Bull 시나리오 설명문 작성

LLM이 목표가, EPS, 배수, 점수 같은 수치를 새로 만들어내면 안 됩니다. LLM 출력은 저장 전 JSON schema, 금지 표현, 수치 출처 검증을 통과해야 합니다.

## 분석 방향

초기 분석기는 아래 순서로 확장합니다.

1. PDF 3~5개 fixture로 추출 필드 채움률 확인
2. `target_price`, `forward_eps_est` 기반 `implied_multiple` 계산
3. 증권사 간 내재 배수 분산 산출
4. 피어 그룹 후보와 비교 배수 기준선 연결
5. category tag 변화와 DART/DataLab 선행 신호 교차 확인
6. 단일 목표가가 아니라 Bear/Base/Bull 데이터 시나리오 밴드 생성

Bear/Base/Bull은 투자 행동 제안이 아닙니다. 각 시나리오는 “데이터가 보여주는 가정 범위”로만 표현합니다.

## 교차 확인 프레임

리포트 배수 변화는 대체로 후행 확인 성격이 강합니다. 따라서 단독 신호로 사용하지 않고 아래 구조에 편입합니다.

선행 후보:

- DART 신사업, 정관 변경, 공급계약, 설비투자 관련 공시
- DataLab 검색 트렌드 변화
- Hiring, Patent의 신규 카테고리 변화
- 초기 1~2개 증권사의 밸류에이션 가정 변화

확인 후보:

- 다수 증권사의 내재 배수 상향 또는 분산 축소
- Price와 수급 데이터의 반영 여부
- 피어 배수 대비 갭 축소 또는 확대

최종 사용자-facing 결과는 소스 간 일치도, 근거, 추가 확인 필요 여부로 표현합니다.

## LangGraph 도입 기준

초기 구현은 LangGraph 없이 진행합니다.

- PDF별 구조화 추출: 코드 + LLM client 단일 호출
- 수치 계산: 코드
- 단순 합성: 정리된 표를 LLM 1회 호출

LangGraph는 아래 요구가 생길 때 검토합니다.

- 글로벌 피어 배수 fetch
- 과거 유사 re-rating 사례 RAG
- DART/DataLab/Price를 동적으로 재조회하는 루프
- 근거 부족 시 제한된 횟수로 재수집 또는 재검색하는 control flow

## 백테스트 원칙

이 전략은 성공 사례만 보면 생존편향이 커집니다. 채택 전에 실패 내러티브를 반드시 포함해 검증합니다.

검증 기준:

- 재평가 신호가 사후 설명이 아니라 사전에 발생했는지
- DART/DataLab/Price 대비 증분 lift가 있는지
- 내재 배수 변화가 이후 데이터 방향성과 어떤 관계를 보였는지
- 실패 사례에서 과도한 thesis 생성이 없었는지

## 개발 순서

1. `report_valuation_facts` 스키마 설계 - 구현 완료
2. PDF fixture 기반 valuation extractor 테스트 추가 - 구현 완료
3. `target_price`, `forward_eps_est`, `implied_multiple` 결정론 계산 구현 - 구현 완료
4. LLM 분류/패러프레이즈 보강 연결 - 구현 완료
5. valuation analyzer에서 배수 분산과 peer gap 산출 - MVP 구현 완료
6. Report Agent와 Aggregator에 valuation fact를 보조 근거로 연결 - 구현 완료
7. 데이터 시나리오 밴드 MVP 생성 - 구현 완료
   - `report_quant.valuation.scenario_band`에 `low_multiple`, `base_multiple`, `high_multiple`, `dispersion_level`, `confidence_note`, `needs_review`를 저장합니다.
   - 수치는 내재 배수 중앙값과 분산 기반 범위로 계산하며, 투자 행동 제안으로 노출하지 않습니다.
8. 백테스트 fixture에 확인/미확인 사례를 함께 추가 - 구현 완료
   - `services/agent-worker/tests/fixtures/report/valuation_backtest_cases.json`에 확인 사례와 미확인 사례를 함께 둡니다.
   - `evaluate_valuation_backtest_case`는 valuation summary와 사후 관찰의 일치/충돌 수를 기반으로 fixture 기대 결과를 검증합니다.
   - fixture는 투자 성과가 아니라 데이터 방향성 유지 여부와 추가 확인 필요 상태를 검증합니다.
9. 수집 파이프라인 형태 valuation 샘플을 백테스트 case로 변환 - 구현 완료
   - `services/agent-worker/tests/fixtures/report/valuation_collection_samples.json`에 `raw_documents`, `report_raw_details`, `report_valuation_facts` 형태의 샘플을 둡니다.
   - `build_valuation_backtest_case_from_sample`은 수집·파싱 산출물 형태의 샘플을 기존 백테스트 evaluator 입력으로 변환합니다.
   - 실제 운영 DB row 추출은 후속 작업으로 분리합니다.
