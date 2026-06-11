"""
네이버 증권 리포트 목록 크롤러
Signal α — Report RAG 데이터 수집용

수집 대상:
  - 종목: 삼성전자(005930) / SK하이닉스(000660) / 네이버(035420)
  - 증권사: 신한투자증권 / 미래에셋증권 / 유진투자증권
  - 유형: Earnings Review / Event Note / Company Report / Earnings Preview
  - 기간: 2025.07.01 ~ 2025.09.30
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────

STOCKS = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "네이버": "035420",
}

TARGET_FIRMS = ["신한투자증권", "미래에셋증권", "유진투자증권"]

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

EXCLUDE_KEYWORDS = [
    "신규", "개시", "Initiating", "TP 변경", "목표주가 변경",
    "Valuation Update", "TP Change",
]

# 수집 기간 (고정)
DATE_START = datetime(2025, 7, 1)
DATE_END = datetime(2025, 9, 30, 23, 59, 59)

NAVER_FINANCE_BASE = "https://finance.naver.com"
NAVER_RESEARCH_BASE = f"{NAVER_FINANCE_BASE}/research/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": NAVER_FINANCE_BASE,
}

ROOT_DIR = Path(__file__).resolve().parents[5]
DATA_DIR = ROOT_DIR / "data"


# ──────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────

def clean_price(raw: str) -> int | None:
    cleaned = raw.replace(",", "").replace("원", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def normalize_opinion(raw: str) -> str:
    mapping = {
        "매수": "buy",
        "BUY": "buy",
        "매수(유지)": "buy",
        "매수 유지": "buy",
        "강력매수": "strong_buy",
        "중립": "neutral",
        "HOLD": "neutral",
        "보유": "neutral",
        "매도": "sell",
        "SELL": "sell",
    }
    return mapping.get(raw.strip(), "unknown")


def classify_report_type(title: str) -> str | None:
    for kw in EXCLUDE_KEYWORDS:
        if kw in title:
            return None

    for report_type, keywords in REPORT_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                return report_type

    return "company_report"


def parse_date(date_str: str) -> datetime | None:
    """네이버 작성일: '25.07.25' 또는 '2025.07.25'"""
    for fmt in ("%y.%m.%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def is_within_range(date_str: str) -> bool:
    """날짜가 수집 기간(2025.07.01 ~ 2025.09.30) 이내인지 확인"""
    dt = parse_date(date_str)
    if not dt:
        return False
    return DATE_START <= dt <= DATE_END


# ──────────────────────────────────────────
# 크롤링 함수
# ──────────────────────────────────────────

def fetch_page(stock_code: str, page: int) -> str:
    url = "https://finance.naver.com/research/company_list.naver"
    params = {
        "searchType": "itemCode",
        "itemCode": stock_code,
        "page": page,
    }

    response = requests.get(url, params=params, headers=HEADERS, timeout=10)
    response.encoding = "euc-kr"
    return response.text


def parse_reports(html: str) -> tuple[list[dict], bool]:
    """
    HTML에서 리포트 메타데이터 추출

    Returns:
        (reports, stop_paging): stop_paging이 True면 이후 페이지는 기간 밖
    """
    soup = BeautifulSoup(html, "html.parser")
    reports = []
    stop_paging = False

    table = soup.select_one("table.type_1")
    if not table:
        print("  [경고] 테이블을 찾지 못했습니다. HTML 구조가 바뀌었을 수 있습니다.")
        return reports, True

    for row in table.select("tr"):
        cols = row.select("td")
        if len(cols) < 6:
            continue

        try:
            date_str = cols[4].get_text(strip=True)
            report_date = parse_date(date_str)

            # 수집 시작일 이전 리포트가 나오면 이후 페이지는 더 오래됨
            if report_date and report_date < DATE_START:
                stop_paging = True
                continue

            # 테이블 구조: 종목명(0) | 제목(1) | 증권사(2) | 첨부(3) | 작성일(4) | 조회수(5)
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
            # Naver 리포트 상세 페이지 URL
            pdf_url = urljoin(NAVER_RESEARCH_BASE, pdf_href) if pdf_href else None

            # 첨부 컬럼(3)에서 직접 PDF 다운로드 URL 추출
            attach_tag = cols[3].select_one("a")
            pdf_direct_url = attach_tag.get("href") if attach_tag else None

            # 목록 페이지에는 목표주가·투자의견 없음 → PDF 파싱 단계에서 채움
            target_price = None
            opinion_raw = ""

            report_type = classify_report_type(title)
            if report_type is None:
                continue

            reports.append({
                "firm": firm,
                "title": title,
                "opinion": normalize_opinion(opinion_raw) if opinion_raw else "unknown",
                "opinion_raw": opinion_raw,
                "target_price": target_price,
                "date": date_str,
                "pdf_url": pdf_url,
                "pdf_direct_url": pdf_direct_url,
                "report_type": report_type,
                "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "processed": False,
            })

        except Exception as e:
            print(f"  [오류] 행 파싱 실패: {e}")
            continue

    return reports, stop_paging


def collect_stock(stock_name: str, stock_code: str, max_pages: int = 20) -> list[dict]:
    all_reports = []
    print(f"\n[{stock_name}] 수집 시작 (코드: {stock_code})")

    for page in range(1, max_pages + 1):
        print(f"  page {page} 수집 중...", end=" ")

        try:
            html = fetch_page(stock_code, page)
            reports, stop_paging = parse_reports(html)

            if not reports:
                if stop_paging:
                    print("수집 시작일 이전 도달 → 중단")
                    break
                print("해당 페이지 매칭 없음 → 다음 페이지")
                time.sleep(1.5)
                continue

            for r in reports:
                r["stock_name"] = stock_name
                r["stock_code"] = stock_code

            all_reports.extend(reports)
            print(f"{len(reports)}건 수집")

            if stop_paging:
                print("  기간 시작일 이전 리포트 도달 → 중단")
                break

        except Exception as e:
            print(f"오류 발생: {e}")
            break

        time.sleep(1.5)

    print(f"  [{stock_name}] 완료: 총 {len(all_reports)}건")
    return all_reports


def collect_all(max_pages: int = 20) -> pd.DataFrame:
    all_reports = []

    for stock_name, stock_code in STOCKS.items():
        reports = collect_stock(stock_name, stock_code, max_pages)
        all_reports.extend(reports)

    df = pd.DataFrame(all_reports)

    if df.empty:
        print("\n수집된 데이터가 없습니다.")
        return df

    df = df.drop_duplicates(subset=["pdf_url"], keep="first")
    df = df.sort_values("date", ascending=False).reset_index(drop=True)
    return df


def save_results(df: pd.DataFrame) -> None:
    if df.empty:
        print("저장할 데이터가 없습니다.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = DATA_DIR / "report_list.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nCSV 저장 완료: {csv_path}")

    json_path = DATA_DIR / "report_list.json"
    records = df.to_dict(orient="records")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"JSON 저장 완료: {json_path}")


def print_summary(df: pd.DataFrame) -> None:
    if df.empty:
        return

    print("\n" + "=" * 60)
    print("수집 결과 요약")
    print("=" * 60)
    print(f"수집 기간: {DATE_START.strftime('%Y.%m.%d')} ~ {DATE_END.strftime('%Y.%m.%d')}")
    print(f"총 수집: {len(df)}건")
    print()

    print("[종목별]")
    print(df.groupby("stock_name").size().to_string())
    print()

    print("[리포트 유형별]")
    print(df.groupby("report_type").size().to_string())
    print()

    print("[증권사별]")
    print(df.groupby("firm").size().to_string())
    print()

    print("[최신 10건]")
    cols = ["stock_name", "firm", "report_type", "title", "opinion", "target_price", "date"]
    print(df[cols].head(10).to_string(index=False))
    print("=" * 60)


if __name__ == "__main__":
    print("=" * 60)
    print("Signal α — 증권사 리포트 크롤러")
    print(f"수집 기간: {DATE_START.strftime('%Y.%m.%d')} ~ {DATE_END.strftime('%Y.%m.%d')}")
    print(f"수집 증권사: {', '.join(TARGET_FIRMS)}")
    print(f"수집 종목: {', '.join(STOCKS.keys())}")
    print("=" * 60)

    df = collect_all(max_pages=20)
    save_results(df)
    print_summary(df)
