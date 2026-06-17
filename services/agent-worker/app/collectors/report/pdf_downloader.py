"""
PDF 다운로더
naver_report_crawler(crawler.py)가 수집한 report_list.json에서
pdf_direct_url → data/reports/{종목폴더}/{파일명}.pdf 자동 다운로드

실행:
  python pdf_downloader.py                # 전체 다운로드
  python pdf_downloader.py --incremental  # 이미 있는 파일 스킵
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.collectors.report.s3_client import ReportS3Client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

ROOT_DIR = Path(__file__).resolve().parents[5]
DATA_DIR = ROOT_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"
LIST_PATH = DATA_DIR / "report_list.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com",
}

STOCK_FOLDER_MAP = {
    "005930": "samsung",
    "000660": "skhynix",
    "035420": "naver",
}

FIRM_CODE_MAP = {
    "신한투자증권": "shinhan",
    "미래에셋증권": "mirae",
    "유진투자증권": "eugene",
}

REPORT_TYPE_CODE = {
    "company_report": "cr",
    "earnings_review": "er",
    "event_note": "en",
    "earnings_preview": "ep",
}


def make_filename(report: dict) -> str:
    """리포트 메타 → 파일명: mirae_20250714_cr.pdf"""
    firm_code = FIRM_CODE_MAP.get(report.get("firm", ""), "unknown")
    date_str = report.get("date", "").replace(".", "")   # "2025.07.14" → "20250714"
    type_code = REPORT_TYPE_CODE.get(report.get("report_type", ""), "cr")
    return f"{firm_code}_{date_str}_{type_code}.pdf"


def make_s3_key(stock_code: str, filename: str) -> str:
    return f"reports/{stock_code}/{filename}"


def download_and_upload(url: str, s3_key: str, s3_client: ReportS3Client) -> bool:
    """PDF URL → S3 업로드. 성공 시 True 반환."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            return False
        pdf_bytes = b"".join(resp.iter_content(chunk_size=8192))
        if len(pdf_bytes) < 1024:
            return False
        s3_client.upload_pdf(pdf_bytes, s3_key)
        return True
    except Exception as e:
        print(f"    오류: {e}")
        return False


def download_pdf(url: str, dest: Path) -> bool:
    """PDF URL → 파일 저장. 성공 시 True 반환."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return dest.stat().st_size > 1024  # 1KB 이상이면 유효
    except Exception as e:
        print(f"    오류: {e}")
        return False


def run(incremental: bool = False) -> None:
    if not LIST_PATH.exists():
        print(f"[오류] {LIST_PATH} 없음. 먼저 crawler.py를 실행하세요.")
        sys.exit(1)

    with open(LIST_PATH, encoding="utf-8") as f:
        reports: list[dict] = json.load(f)

    total = len(reports)
    downloaded = skipped = failed = no_url = 0

    print(f"총 {total}건 다운로드 시작\n")

    for i, report in enumerate(reports, 1):
        stock_code = report.get("stock_code", "")
        stock_folder = STOCK_FOLDER_MAP.get(stock_code, stock_code)
        filename = make_filename(report)
        dest = REPORTS_DIR / stock_folder / filename

        label = f"[{i}/{total}] {report.get('firm')} {report.get('date')} {report.get('stock_name')}"

        # 직접 PDF URL 없으면 스킵
        pdf_url = report.get("pdf_direct_url")
        if not pdf_url:
            print(f"{label} → URL 없음 (크롤러 재실행 필요)")
            no_url += 1
            continue

        # 증분 모드: 이미 파일 있으면 스킵
        if incremental and dest.exists():
            print(f"{label} → 스킵 (이미 있음: {filename})")
            report["pdf_file"] = str(dest.relative_to(ROOT_DIR)).replace("\\", "/")
            skipped += 1
            continue

        # 다운로드
        success = download_pdf(pdf_url, dest)
        if success:
            print(f"{label} → 저장: {filename}")
            report["pdf_file"] = str(dest.relative_to(ROOT_DIR)).replace("\\", "/")
            downloaded += 1
        else:
            print(f"{label} → 실패")
            failed += 1

        time.sleep(1.0)

    # report_list.json에 pdf_file 경로 업데이트
    with open(LIST_PATH, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"완료  : {downloaded}건 다운로드")
    print(f"스킵  : {skipped}건 (이미 있음)")
    print(f"실패  : {failed}건")
    print(f"URL없음: {no_url}건 (크롤러 재실행 필요)")
    print(f"저장 위치: {REPORTS_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Naver 증권사 리포트 PDF 다운로더")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="이미 다운로드된 파일은 스킵",
    )
    args = parser.parse_args()
    run(incremental=args.incremental)
