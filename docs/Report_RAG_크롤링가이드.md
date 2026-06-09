# Report RAG — 크롤링 구현 가이드

> **팀 LENS | Signal α**
> 네이버 증권 리포트 목록 자동 수집 전체 구현 가이드

---

## 목차

1. 전체 구조 개요
2. 환경 세팅
3. 폴더 구조
4. 구현 코드 전체
5. 실행 방법
6. 결과물 확인
7. 트러블슈팅

---

## 1. 전체 구조 개요

```
네이버 증권 리포트 목록 페이지
        ↓  requests로 HTML 요청
HTML 수신 (table.type_1)
        ↓  BeautifulSoup으로 파싱
리포트 메타데이터 추출
(증권사명, 제목, 날짜, 목표주가, 투자의견, PDF 링크)
        ↓  필터링
수집 대상 4종만 분류
(Earnings Review / Event Note / Company Report / Earnings Preview)
        ↓  저장
report_list.csv + report_list.json
```

### 수집 대상

| 항목 | 값 |
|---|---|
| 종목 | 삼성전자(005930) / SK하이닉스(000660) / 네이버(035420) |
| 증권사 | 신한투자증권 / 미래에셋증권 / 유진투자증권 |
| 리포트 유형 | Earnings Review / Event Note / Company Report / Earnings Preview |
| 제외 유형 | Initiating Coverage / Target Price Change |
| 수집 기간 | **2025.07.01 ~ 2025.09.30** (고정) |

---

## 2. 환경 세팅

### 라이브러리 설치

```bash
pip install requests beautifulsoup4 pandas
```

### 설치 확인

```python
import requests
import bs4
import pandas
print("OK")
```

---

## 3. 폴더 구조

```
signal-alpha/
├── crawlers/
│   └── naver_report_crawler.py     ← 크롤러 메인 파일
├── data/
│   ├── reports/
│   │   ├── samsung/                ← 삼성전자 PDF 저장
│   │   ├── skhynix/                ← SK하이닉스 PDF 저장
│   │   └── naver/                  ← 네이버 PDF 저장
│   ├── report_list.csv             ← 수집 목록 (Excel로 열 수 있음)
│   └── report_list.json            ← 수집 목록 (Agent가 읽는 형식)
└── docs/
```

폴더 생성 명령어:

```bash
mkdir -p data/reports/samsung data/reports/skhynix data/reports/naver crawlers
```

---

## 4. 구현 코드 전체

`crawlers/naver_report_crawler.py` 파일로 저장

```python
"""
네이버 증권 리포트 목록 크롤러
Signal α — Report RAG 데이터 수집용

수집 대상:
  - 종목: 삼성전자(005930) / SK하이닉스(000660) / 네이버(035420)
  - 증권사: 신한투자증권 / 미래에셋증권 / 유진투자증권
  - 유형: Earnings Review / Event Note / Company Report / Earnings Preview
"""

import requests
from bs4 import BeautifulSoup
import time
import json
import pandas as pd
from datetime import datetime, timedelta


# ──────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────

STOCKS = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "네이버": "035420",
}

TARGET_FIRMS = ["신한투자증권", "미래에셋증권", "유진투자증권"]

# 제목 키워드로 리포트 유형 분류
REPORT_KEYWORDS = {
    "earnings_review": [
        "실적", "어닝", "Earnings", "Review", "실망", "서프라이즈",
        "1Q", "2Q", "3Q", "4Q", "분기", "잠정",
    ],
    "event_note": [
        "Flash", "Note", "이벤트", "속보", "긴급", "코멘트",
        "뉴스", "공시", "계약", "M&A", "인수",
    ],
    "company_report": [
        "기업분석", "In-depth", "In depth", "분석", "전망", "업데이트",
    ],
    "earnings_preview": [
        "Preview", "전망", "프리뷰", "추정", "예상", "추정치",
    ],
}

# 수집에서 제외할 유형 키워드
EXCLUDE_KEYWORDS = [
    "신규", "개시", "Initiating", "TP 변경", "목표주가 변경",
    "Valuation Update", "TP Change",
]

# 수집 기간 (고정)
DATE_START = datetime(2025, 7, 1)
DATE_END = datetime(2025, 9, 30, 23, 59, 59)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com",
}


# ──────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────

def clean_price(raw: str) -> int | None:
    """
    목표주가 문자열 정제
    "112,000" → 112000
    "-" 또는 빈 값 → None
    """
    cleaned = raw.replace(",", "").replace("원", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def normalize_opinion(raw: str) -> str:
    """
    투자의견 정규화
    매수 / BUY → "buy"
    중립 / HOLD → "neutral"
    매도 / SELL → "sell"
    """
    mapping = {
        "매수":       "buy",
        "BUY":        "buy",
        "매수(유지)": "buy",
        "매수 유지":  "buy",
        "강력매수":   "strong_buy",
        "중립":       "neutral",
        "HOLD":       "neutral",
        "보유":       "neutral",
        "매도":       "sell",
        "SELL":       "sell",
    }
    return mapping.get(raw.strip(), "unknown")


def classify_report_type(title: str) -> str | None:
    """
    제목 키워드로 리포트 유형 분류
    제외 대상이면 None 반환
    """
    # 제외 유형 먼저 체크
    for kw in EXCLUDE_KEYWORDS:
        if kw in title:
            return None

    # 수집 유형 분류
    for report_type, keywords in REPORT_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                return report_type

    # 키워드 매칭 없으면 company_report로 처리
    return "company_report"


def parse_date(date_str: str) -> datetime | None:
    """
    날짜 문자열 → datetime 객체
    네이버 작성일: "25.07.25" 또는 "2025.07.25"
    """
    for fmt in ("%y.%m.%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def is_within_range(date_str: str) -> bool:
    """
    날짜가 수집 기간(2025.07.01 ~ 2025.09.30) 이내인지 확인
    """
    dt = parse_date(date_str)
    if not dt:
        return False
    return DATE_START <= dt <= DATE_END


# ──────────────────────────────────────────
# 크롤링 함수
# ──────────────────────────────────────────

def fetch_page(stock_code: str, page: int) -> str:
    """
    네이버 증권 리포트 목록 페이지 HTML 가져오기

    Args:
        stock_code: 종목코드 (예: "000660")
        page: 페이지 번호 (1부터 시작)

    Returns:
        HTML 문자열
    """
    url = "https://finance.naver.com/research/company_list.naver"
    params = {
        "searchType": "itemCode",
        "itemCode": stock_code,
        "page": page,
    }

    response = requests.get(url, params=params, headers=HEADERS, timeout=10)
    response.encoding = "euc-kr"  # 네이버는 euc-kr 인코딩
    return response.text


def parse_reports(html: str) -> list[dict]:
    """
    HTML에서 리포트 메타데이터 추출

    Args:
        html: 네이버 증권 페이지 HTML

    Returns:
        리포트 딕셔너리 리스트
    """
    soup = BeautifulSoup(html, "html.parser")
    reports = []

    table = soup.select_one("table.type_1")
    if not table:
        print("  [경고] 테이블을 찾지 못했습니다. HTML 구조가 바뀌었을 수 있습니다.")
        return reports

    rows = table.select("tr")

    for row in rows:
        cols = row.select("td")

        # 헤더 행 스킵 (컬럼 수가 6 미만이면 데이터 행 아님)
        if len(cols) < 6:
            continue

        try:
            # 테이블 구조 (2025년 기준):
            # 종목명(0) | 제목(1) | 증권사(2) | 첨부(3) | 작성일(4) | 조회수(5)
            date_str = cols[4].get_text(strip=True)
            report_date = parse_date(date_str)

            if report_date and report_date < DATE_START:
                stop_paging = True
                continue

            firm = cols[2].get_text(strip=True)
            if firm not in TARGET_FIRMS:
                continue

            if not is_within_range(date_str):
                continue

            title_tag = cols[1].select_one("a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            pdf_href = title_tag.get("href", "")
            # href는 research/ 기준 상대경로 → /research/company_read.naver?nid=...
            pdf_url = urljoin("https://finance.naver.com/research/", pdf_href) if pdf_href else None

            # 목록 페이지에는 목표주가·투자의견 없음 → PDF 파싱 단계에서 채움
            target_price = None
            opinion_raw = ""

            # 리포트 유형 분류
            report_type = classify_report_type(title)

            # 제외 대상이면 스킵
            if report_type is None:
                continue

            report = {
                "firm":         firm,
                "title":        title,
                "opinion":      normalize_opinion(opinion_raw),
                "opinion_raw":  opinion_raw,
                "target_price": target_price,
                "date":         date_str,
                "pdf_url":      pdf_url,
                "report_type":  report_type,
                "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "processed":    False,
            }

            reports.append(report)

        except Exception as e:
            print(f"  [오류] 행 파싱 실패: {e}")
            continue

    return reports


def collect_stock(stock_name: str, stock_code: str, max_pages: int = 10) -> list[dict]:
    """
    단일 종목 전체 페이지 수집

    Args:
        stock_name: 종목 이름 (예: "SK하이닉스")
        stock_code: 종목코드 (예: "000660")
        max_pages: 최대 수집 페이지 수

    Returns:
        해당 종목 리포트 리스트
    """
    all_reports = []
    print(f"\n[{stock_name}] 수집 시작 (코드: {stock_code})")

    for page in range(1, max_pages + 1):
        print(f"  page {page} 수집 중...", end=" ")

        try:
            html = fetch_page(stock_code, page)
            reports = parse_reports(html)

            if not reports:
                print("데이터 없음 → 중단")
                break

            # 종목 정보 추가
            for r in reports:
                r["stock_name"] = stock_name
                r["stock_code"] = stock_code

            all_reports.extend(reports)
            print(f"{len(reports)}건 수집")

        except Exception as e:
            print(f"오류 발생: {e}")
            break

        # 요청 간격 (IP 차단 방지)
        time.sleep(1.5)

    print(f"  [{stock_name}] 완료: 총 {len(all_reports)}건")
    return all_reports


def collect_all(max_pages: int = 10) -> pd.DataFrame:
    """
    전체 종목 수집 실행

    Args:
        max_pages: 종목당 최대 수집 페이지

    Returns:
        전체 수집 결과 DataFrame
    """
    all_reports = []

    for stock_name, stock_code in STOCKS.items():
        reports = collect_stock(stock_name, stock_code, max_pages)
        all_reports.extend(reports)

    df = pd.DataFrame(all_reports)

    if df.empty:
        print("\n수집된 데이터가 없습니다.")
        return df

    # 중복 제거 (동일 PDF URL이면 하나만 유지)
    df = df.drop_duplicates(subset=["pdf_url"], keep="first")

    # 날짜 역순 정렬
    df = df.sort_values("date", ascending=False).reset_index(drop=True)

    return df


def save_results(df: pd.DataFrame) -> None:
    """
    수집 결과 CSV + JSON 저장
    """
    if df.empty:
        print("저장할 데이터가 없습니다.")
        return

    # CSV 저장 (Excel로 열 수 있음)
    csv_path = "data/report_list.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nCSV 저장 완료: {csv_path}")

    # JSON 저장 (Agent가 읽는 형식)
    json_path = "data/report_list.json"
    records = df.to_dict(orient="records")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"JSON 저장 완료: {json_path}")


def print_summary(df: pd.DataFrame) -> None:
    """
    수집 결과 요약 출력
    """
    if df.empty:
        return

    print("\n" + "=" * 60)
    print("수집 결과 요약")
    print("=" * 60)
    print(f"총 수집: {len(df)}건")
    print()

    # 종목별 집계
    print("[종목별]")
    print(df.groupby("stock_name").size().to_string())
    print()

    # 리포트 유형별 집계
    print("[리포트 유형별]")
    print(df.groupby("report_type").size().to_string())
    print()

    # 증권사별 집계
    print("[증권사별]")
    print(df.groupby("firm").size().to_string())
    print()

    # 상위 10건 미리보기
    print("[최신 10건]")
    cols = ["stock_name", "firm", "report_type", "title", "opinion", "target_price", "date"]
    print(df[cols].head(10).to_string(index=False))
    print("=" * 60)


# ──────────────────────────────────────────
# 실행
# ──────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Signal α — 증권사 리포트 크롤러")
    print(f"수집 기간: {DATE_START.strftime('%Y.%m.%d')} ~ {DATE_END.strftime('%Y.%m.%d')}")
    print(f"수집 증권사: {', '.join(TARGET_FIRMS)}")
    print(f"수집 종목: {', '.join(STOCKS.keys())}")
    print("=" * 60)

    df = collect_all(max_pages=10)
    save_results(df)
    print_summary(df)
```

---

## 5. 실행 방법

### 전체 실행

```bash
# signal-alpha 루트 디렉토리에서 실행
python crawlers/naver_report_crawler.py
```

### 단계별 테스트 (처음 실행 시 권장)

**STEP 1 — HTML 수신 확인**

```python
from crawlers.naver_report_crawler import fetch_page

html = fetch_page("000660", 1)
print(html[:500])   # 앞 500자 출력 → "<!DOCTYPE html>" 로 시작해야 정상
print(len(html))    # 10000자 이상이면 정상 수신
```

**STEP 2 — 파싱 확인**

```python
from crawlers.naver_report_crawler import fetch_page, parse_reports

html = fetch_page("000660", 1)
reports = parse_reports(html)

print(f"파싱된 리포트 수: {len(reports)}")
for r in reports[:3]:
    print(r)
```

**STEP 3 — 단일 종목 수집 확인**

```python
from crawlers.naver_report_crawler import collect_stock

reports = collect_stock("SK하이닉스", "000660", max_pages=2)
print(f"수집: {len(reports)}건")
```

**STEP 4 — 전체 실행**

```bash
python crawlers/naver_report_crawler.py
```

---

## 6. 결과물 확인

### 예상 출력

```
============================================================
Signal α — 증권사 리포트 크롤러
수집 기간: 2025.07.01 ~ 2025.09.30
수집 증권사: 신한투자증권, 미래에셋증권, 유진투자증권
수집 종목: 삼성전자, SK하이닉스, 네이버
============================================================

[삼성전자] 수집 시작 (코드: 005930)
  page 1 수집 중... 4건 수집
  page 2 수집 중... 3건 수집
  page 3 수집 중... 데이터 없음 → 중단
  [삼성전자] 완료: 총 7건

[SK하이닉스] 수집 시작 (코드: 000660)
  page 1 수집 중... 5건 수집
  ...

CSV 저장 완료: data/report_list.csv
JSON 저장 완료: data/report_list.json

============================================================
수집 결과 요약
============================================================
총 수집: 24건

[종목별]
삼성전자      7
SK하이닉스    10
네이버         7

[리포트 유형별]
company_report      8
earnings_preview    6
earnings_review     8
event_note          2

[증권사별]
유진투자증권    5
미래에셋증권    8
신한투자증권    8
============================================================
```

### 저장된 CSV 구조

| 컬럼 | 설명 | 예시 |
|---|---|---|
| `stock_name` | 종목명 | 삼성전자 |
| `stock_code` | 종목코드 | 005930 |
| `firm` | 증권사명 | 신한투자증권 |
| `title` | 리포트 제목 | 1Q26 Earnings Review |
| `opinion` | 투자의견 (정규화) | buy |
| `opinion_raw` | 투자의견 (원문) | 매수 |
| `target_price` | 목표주가 | 98000 |
| `date` | 발간일 | 2026.05.02 |
| `pdf_url` | 네이버 PDF 링크 | https://... |
| `report_type` | 리포트 유형 분류 | earnings_review |
| `collected_at` | 수집 일시 | 2026-06-08 07:00:00 |
| `processed` | LLM 파싱 완료 여부 | False |

---

## 7. 트러블슈팅

### 테이블을 찾지 못했습니다 경고가 뜨는 경우

네이버 HTML 구조가 바뀌었을 가능성이 있습니다.

```python
# HTML 전체 출력해서 구조 확인
html = fetch_page("000660", 1)
print(html)
# 브라우저 개발자 도구(F12)에서 실제 구조와 비교
```

확인 포인트:
- `table.type_1` 이 여전히 존재하는지
- `td` 순서 (증권사가 몇 번째 칸인지)

### 수집 건수가 0인 경우

```python
# User-Agent 없이 요청해보기 (차단 여부 확인)
import requests
r = requests.get(
    "https://finance.naver.com/research/company_list.naver",
    params={"searchType": "itemCode", "itemCode": "000660", "page": "1"}
)
r.encoding = "euc-kr"
print(r.text[:200])
```

- 빈 HTML이나 에러 페이지가 오면 → User-Agent 확인
- 정상 HTML이 오면 → 파싱 로직 점검

### 날짜 범위 밖 데이터만 있는 경우

수집 기간은 고정 구간입니다. 변경이 필요하면 크롤러 파일에서 아래 값을 수정하세요:

```python
DATE_START = datetime(2025, 7, 1)
DATE_END = datetime(2025, 9, 30, 23, 59, 59)
```

### IP 차단 의심 시

`time.sleep` 값을 늘립니다:

```python
time.sleep(3.0)  # 1.5초 → 3초로 증가
```

---

## 배치 실행 (매일 자동화)

크롤러를 매일 오전 7시 자동 실행하려면 아래 설정을 사용합니다.

### Windows 작업 스케줄러

```
작업 이름: SignalAlpha_ReportCrawler
트리거: 매일 오전 7:00
동작: python C:\...\signal-alpha\crawlers\naver_report_crawler.py
시작 위치: C:\...\signal-alpha
```

### 실행 확인용 로그 추가 (선택)

```bash
python crawlers/naver_report_crawler.py >> logs/crawler.log 2>&1
```

---

*팀 LENS — Link · Evidence · Navigate · Signal*
