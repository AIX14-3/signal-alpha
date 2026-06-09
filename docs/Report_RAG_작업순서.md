# Report RAG — 작업 순서 정의서

> **팀 LENS | Signal α**
> Report RAG 구현을 위한 단계별 작업 순서 및 체크리스트

---

## 핵심 전제 — 뽑아야 하는 데이터 3가지

| # | 항목 | 설명 |
|---|---|---|
| 1 | **목표주가** | 증권사가 제시한 목표 가격 (얼마로 보는가) |
| 2 | **투자의견** | 매수 / 중립 / 매도 (어떻게 보는가) |
| 3 | **핵심 근거** | 그렇게 판단한 이유 (왜 그렇게 보는가) |

> 이 3가지가 없는 리포트는 수집하지 않는다.

---

## 수집 판단 기준 — 리포트별 3가지 충족 여부

> 목표주가·투자의견·핵심 근거 3가지를 기준으로 수집 여부를 결정한다.

| 리포트 유형 | 목표주가 | 투자의견 | 핵심 근거 | 결론 |
|---|---|---|---|---|
| Earnings Review (실적 분석) | ✅ 있음 | ✅ 있음 | ✅ 상세함 | **수집** |
| Event Note (이벤트 노트) | 가끔 있음 | 가끔 있음 | ✅ 있음 | **수집** |
| Company Report (정기 분석) | ✅ 있음 | ✅ 있음 | ✅ 상세함 | **수집** |
| Earnings Preview (실적 전망) | ✅ 있음 | ✅ 있음 | ✅ 있음 | **수집** |
| Initiating Coverage | ✅ 있음 | ✅ 있음 | ✅ 매우 상세 | ❌ 무시 (거의 안 나옴) |
| Target Price Change | ✅ 있음 | ✅ 있음 | ❌ 1~2줄뿐 | ❌ 무시 (내용 없음) |

---

## 수집할 리포트 4종 / 무시할 리포트 2종

### ✅ 수집 대상 (4종)

| 리포트 유형 | 발간 시기 | 주기 |
|---|---|---|
| Earnings Review (실적 분석) | 실적 발표 당일 ~ 3영업일 이내 | 분기 4회 |
| Event Note (이벤트 노트) | 공시·뉴스 발생 당일 | 수시 |
| Company Report (정기 기업 분석) | 분기 or 반기 단위 | 분기~반기 1회 |
| Earnings Preview (실적 전망) | 실적 발표 1~2주 전 | 분기 4회 |

### ❌ 무시 대상 (2종)

| 리포트 유형 | 무시 이유 |
|---|---|
| Initiating Coverage | 삼성전자·SK하이닉스는 이미 전 증권사 커버 중. 신규 발간 거의 없음 |
| Target Price Change | 목표주가는 있지만 근거가 1~2줄뿐. RAG 검색 품질 낮음 |

### 수집 증권사 (3개사 우선)

- 신한투자증권
- 미래에셋증권
- 유진투자증권

### 수집 종목 (3개 종목)

- 삼성전자 (005930)
- SK하이닉스 (000660)
- 네이버 (035420)

---

## 전체 작업 순서

```
PHASE 1  환경 세팅          ← 지금 시작
PHASE 2  크롤러 개발         ← 목록 수집 (네이버 증권)
PHASE 3  리포트 분류         ← 4종 필터링 + 제목 기반 자동 분류
PHASE 4  PDF 수집            ← 링크 추출 + 로컬 저장
PHASE 5  LLM 파싱            ← 3가지 핵심 데이터 추출
PHASE 6  벡터 DB 적재         ← RAG 검색 가능 상태로 저장
PHASE 7  배치 스케줄링        ← 자동화 (매일 7시 + 6시간 간격)
```

---

## PHASE 1 — 환경 세팅

### 목표
크롤러 실행 가능한 Python 환경 구축

### 작업 목록

- [ ] 필수 라이브러리 설치
  ```bash
  pip install requests beautifulsoup4 pandas
  ```
- [ ] 프로젝트 폴더 구조 생성
  ```
  signal-alpha/
    crawlers/
      naver_report_crawler.py   ← 크롤러 메인
      utils/
        price_cleaner.py        ← 목표주가 정제
        opinion_normalizer.py   ← 투자의견 정규화
        report_classifier.py    ← 리포트 유형 분류
    data/
      reports/
        samsung/    (005930)
        skhynix/    (000660)
        naver/      (035420)
      report_list.csv           ← 수집 목록 저장
    docs/
  ```
- [ ] 브라우저에서 네이버 증권 리포트 페이지 구조 육안 확인
  ```
  https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=000660&page=1
  ```

---

## PHASE 2 — 크롤러 개발

### 목표
네이버 증권에서 리포트 목록(제목·증권사·목표주가·투자의견·날짜·PDF URL) 수집

### 작업 목록

- [ ] `fetch_page()` 함수 구현 및 테스트
  - EUC-KR 인코딩 처리
  - User-Agent 헤더 설정 (차단 방지)
  - HTML 정상 반환 확인
  ```python
  html = fetch_page("000660", 1)
  print(html[:500])  # 앞 500자 확인
  ```
- [ ] `parse_reports()` 함수 구현 및 테스트
  - `table.type_1` 파싱
  - 증권사명 필터링 (신한·미래에셋·유진만)
  - 제목·PDF URL·목표주가·투자의견·날짜 추출
- [ ] `collect_all()` 함수 구현
  - 3개 종목 × 최대 5페이지 수집
  - `time.sleep(1.5)` 요청 간격 필수 (IP 차단 방지)
  - 결과 DataFrame으로 반환
- [ ] `report_list.csv` 저장 확인

### 예상 출력

```
[삼성전자] 수집 시작...
  page 1 수집 중...
  ...
총 수집: 47건
   stock_name    firm          title             opinion  target_price  date
0    삼성전자  신한투자증권  3Q 실적 Review: 예상 하회  buy       98000  2025.10.10
```

---

## PHASE 3 — 리포트 분류

### 목표
수집된 목록에서 4종 리포트만 추려내고 유형 태깅

### 작업 목록

- [ ] `classify_report_type()` 함수 구현
  - 제목 키워드 기반 자동 분류
  - 매핑 키워드 정의

  | 유형 | 키워드 |
  |---|---|
  | earnings_review | 실적, 어닝, Earnings, Review, 실망, 서프라이즈 |
  | event_note | Flash, Note, 이벤트, 속보, 긴급, 코멘트 |
  | company_report | 기업분석, In-depth, 분석, Coverage |
  | earnings_preview | Preview, 전망, 프리뷰, 추정 |

- [ ] 분류 결과 검증
  - Initiating Coverage 자동 제외 확인
  - Target Price Change 자동 제외 확인
  - 분류 불확실 케이스 → `company_report` 폴백 처리
- [ ] `clean_price()` 함수 구현 (목표주가 정제)
  - `"112,000"` → `112000`
  - `"-"` 또는 빈 값 → `None`
- [ ] `normalize_opinion()` 함수 구현 (투자의견 정규화)
  - `매수 / BUY / 매수(유지)` → `"buy"`
  - `중립 / HOLD / 보유` → `"neutral"`
  - `매도 / SELL` → `"sell"`

---

## PHASE 4 — PDF 수집

### 목표
분류된 리포트의 PDF를 로컬에 저장

### 작업 목록

- [ ] PDF 다운로드 함수 구현
  - 네이버 리포트 PDF URL 구조 파악
  - 실제 PDF URL 추출 (리다이렉트 처리 필요할 수 있음)
  - 로컬 저장 경로 규칙 적용
    ```
    /data/reports/{종목폴더}/{증권사}_{날짜}_{리포트유형}.pdf
    예: shinhan_20260501_earnings_review.pdf
    ```
- [ ] 중복 수집 방지 처리
  - `pdf_url` 기준 중복 확인
  - 이미 저장된 파일이면 스킵
- [ ] 초기 수동 수집 (자동화 전 테스트용)
  - 신한·미래에셋·유진 × 3종목 × **2025.07.01 ~ 2025.09.30**
  - **목표: 45~90개 PDF**

> **저작권 주의**: 수집한 PDF는 내부 분석용으로만 사용

---

## PHASE 5 — LLM 파싱 (핵심 3가지 추출)

### 목표
PDF 텍스트에서 목표주가·투자의견·핵심 근거 자동 추출

### 작업 목록

- [ ] PDF 텍스트 추출 라이브러리 선택
  - `pdfplumber` 또는 `pymupdf` (fitz) 중 선택
  - 한국어 리포트 텍스트 추출 품질 테스트
- [ ] LLM 파싱 프롬프트 설계
  - 입력: 리포트 전체 텍스트
  - 출력 스키마:
    ```json
    {
      "target_price": 112000,
      "opinion": "buy",
      "key_rationale": "HBM 시장 점유율 확대로 ASP 상승 예상. 3Q 영업이익 컨센서스 상회."
    }
    ```
- [ ] 파싱 실패 케이스 처리
  - 목표주가 없으면 크롤링 메타데이터 값 사용
  - 핵심 근거 추출 실패 시 제목 + 첫 단락으로 대체
- [ ] 파싱 결과 품질 검증 (샘플 10개 육안 확인)

---

## PHASE 6 — 벡터 DB 적재

### 목표
파싱된 리포트를 RAG 검색 가능한 벡터 DB에 저장

### 작업 목록

- [ ] 벡터 DB 선택 및 세팅
  - 후보: `ChromaDB` (로컬), `Pinecone` (클라우드)
  - 6주 프로젝트 특성상 ChromaDB 로컬 우선 검토
- [ ] 청크 전략 설계
  - 리포트 전체를 하나의 문서로 적재 vs 섹션별 분할
  - 메타데이터 필드 정의: `stock_code, firm, report_type, date, target_price, opinion`
- [ ] 임베딩 모델 선택
  - `text-embedding-3-small` (OpenAI) 또는 한국어 특화 모델
- [ ] RAG 검색 테스트
  - 쿼리: "SK하이닉스 목표주가 최신 증권사 의견"
  - 반환 결과 관련성 확인

---

## PHASE 7 — 배치 스케줄링

### 목표
수집·파싱·적재 파이프라인 자동화

### 작업 목록

- [ ] 일반 리포트 배치 스케줄러 설정
  - 매일 오전 7시 실행
  - 전날 올라온 리포트 일괄 수집
- [ ] Event Note 스케줄러 설정
  - 6시간 간격 실행 (0시·6시·12시·18시)
  - 공시 발생 당일 수집을 위한 빠른 주기
- [ ] 오래된 리포트 자동 아카이브
  - 90일 초과 리포트 → `archived` 상태로 전환
  - 종목당 최대 20개, 증권사당 최신 3개 유지
- [ ] 스케줄러 실행 로그 및 알림 설정

---

## 현재 할 일 체크리스트 (지금 바로 시작)

> PHASE 1 ~ 2 순서대로 진행

### 즉시 실행

- [ ] **STEP 1**: `pip install requests beautifulsoup4 pandas` 설치
- [ ] **STEP 2**: 브라우저에서 URL 열어서 HTML 구조 육안 확인
  ```
  https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=000660&page=1
  ```
- [ ] **STEP 3**: `fetch_page()` 함수만 먼저 실행해서 HTML 정상 수신 확인
  ```python
  html = fetch_page("000660", 1)
  print(html[:500])
  ```

### 크롤러 파일 생성

- [ ] `crawlers/naver_report_crawler.py` 파일 생성
- [ ] 전체 함수 구현 및 단위 테스트
  - `fetch_page()` ← 먼저
  - `parse_reports()` ← 다음
  - `clean_price()` / `normalize_opinion()` / `classify_report_type()`
  - `collect_all()` ← 마지막
- [ ] `collect_all(max_pages=5)` 실행 → `report_list.csv` 저장 확인

---

## 수동 PDF 수집 목록 (초기 RAG 구성용)

> **수집 기간**: **2025.07.01 ~ 2025.09.30** (고정)
> **목표**: 45~90개 PDF
> **저장 경로**: `data/reports/{종목폴더}/{증권사코드}_{날짜}_{리포트유형}.pdf`
> **다운로드 위치**: [네이버 증권 리포트](https://finance.naver.com/research/company_list.naver)

### 파일명 증권사 코드 규칙

| 증권사 | 코드 |
|---|---|
| 신한투자증권 | `shinhan` |
| 미래에셋증권 | `mirae` |
| 유진투자증권 | `eugene` |

### 파일명 리포트 유형 코드 규칙

| 리포트 유형 | 코드 |
|---|---|
| Earnings Review | `er` |
| Event Note | `en` |
| Company Report | `cr` |
| Earnings Preview | `ep` |

### 파일명 예시

```
shinhan_20250725_er.pdf     ← 신한투자증권, 7/25, Earnings Review
mirae_20250810_ep.pdf       ← 미래에셋증권, 8/10, Earnings Preview
eugene_20250901_cr.pdf      ← 유진투자증권, 9/1, Company Report
```

---

### 삼성전자 (005930) — 저장 경로: `data/reports/samsung/`

#### 2025년 3분기 수집 구간 (2025.07.01 ~ 2025.09.30)

| # | 리포트 유형 | 발간 시기 (해당 기간) | 신한투자증권 | 미래에셋증권 | 유진투자증권 |
|---|---|---|---|---|---|
| 1 | Earnings Preview | 7월 초~중순 (2Q 실적 전) | - [ ] | - [ ] | - [ ] |
| 2 | Earnings Review | 7월 말~8월 초 (2Q 실적 후) | - [ ] | - [ ] | - [ ] |
| 3 | Company Report | 8~9월 (실적 반영 후) | - [ ] | - [ ] | - [ ] |
| 4 | Event Note | 기간 내 공시 발생 시 | - [ ] | - [ ] | - [ ] |

**소계 목표: 9~12개**

#### 접근 URL

```
https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=005930&page=1
```

---

### SK하이닉스 (000660) — 저장 경로: `data/reports/skhynix/`

#### 2025년 3분기 수집 구간 (2025.07.01 ~ 2025.09.30)

| # | 리포트 유형 | 발간 시기 (해당 기간) | 신한투자증권 | 미래에셋증권 | 유진투자증권 |
|---|---|---|---|---|---|
| 1 | Earnings Preview | 7월 초~중순 (2Q 실적 전) | - [ ] | - [ ] | - [ ] |
| 2 | Earnings Review | 7월 말~8월 초 (2Q 실적 후) | - [ ] | - [ ] | - [ ] |
| 3 | Company Report | 8~9월 (실적 반영 후) | - [ ] | - [ ] | - [ ] |
| 4 | Event Note | 기간 내 공시 발생 시 | - [ ] | - [ ] | - [ ] |

**소계 목표: 9~12개**

#### 접근 URL

```
https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=000660&page=1
```

---

### 네이버 (035420) — 저장 경로: `data/reports/naver/`

#### 2025년 3분기 수집 구간 (2025.07.01 ~ 2025.09.30)

| # | 리포트 유형 | 발간 시기 (해당 기간) | 신한투자증권 | 미래에셋증권 | 유진투자증권 |
|---|---|---|---|---|---|
| 1 | Earnings Preview | 7월 초~중순 (2Q 실적 전) | - [ ] | - [ ] | - [ ] |
| 2 | Earnings Review | 7월 말~8월 초 (2Q 실적 후) | - [ ] | - [ ] | - [ ] |
| 3 | Company Report | 8~9월 (실적 반영 후) | - [ ] | - [ ] | - [ ] |
| 4 | Event Note | 기간 내 공시 발생 시 | - [ ] | - [ ] | - [ ] |

**소계 목표: 9~12개**

#### 접근 URL

```
https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=035420&page=1
```

---

### 수동 수집 총괄 현황

| 종목 | Earnings Preview | Earnings Review | Company Report | Event Note | 소계 |
|---|---|---|---|---|---|
| 삼성전자 | 3개 (3사) | 3개 (3사) | 3개 (3사) | 있으면 수집 | **9개 이상** |
| SK하이닉스 | 3개 (3사) | 3개 (3사) | 3개 (3사) | 있으면 수집 | **9개 이상** |
| 네이버 | 3개 (3사) | 3개 (3사) | 3개 (3사) | 있으면 수집 | **9개 이상** |
| **합계** | **9개** | **9개** | **9개** | **수시** | **최소 27개** |

> Event Note 포함 시 **45~90개** 범위 달성 가능

### 수동 수집 절차

```
1. 아래 네이버 증권 URL 종목별로 접속
2. 증권사 필터: 신한투자증권 / 미래에셋증권 / 유진투자증권
3. 리포트 유형 확인: 제목에서 4종 해당 여부 확인
4. PDF 다운로드 버튼 클릭 → 로컬 저장
5. 파일명 규칙에 맞게 rename
6. 지정 폴더에 이동
```

### 수집 우선순위

```
1순위  Earnings Review   ← 실적 직후 발간. 목표주가·근거 가장 상세
2순위  Company Report    ← 분기 전반 정리. 재무 모델 포함
3순위  Earnings Preview  ← 실적 전 추정치. 컨센서스 기준선
4순위  Event Note        ← 공시 직결. 있으면 반드시 수집
```

---

## 작업 진행 상태

| PHASE | 내용 | 상태 |
|---|---|---|
| PHASE 1 | 환경 세팅 | 🔲 미시작 |
| PHASE 2 | 크롤러 개발 | 🔲 미시작 |
| PHASE 3 | 리포트 분류 | 🔲 미시작 |
| PHASE 4 | PDF 수집 | 🔲 미시작 |
| PHASE 5 | LLM 파싱 | 🔲 미시작 |
| PHASE 6 | 벡터 DB 적재 | 🔲 미시작 |
| PHASE 7 | 배치 스케줄링 | 🔲 미시작 |

---

*팀 LENS — Link · Evidence · Navigate · Signal*
