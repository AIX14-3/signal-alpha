"""
PDF 리포트 일괄 파싱
- PDF 텍스트 추출 → LLM 파싱 → parsed_reports.json 저장
"""
import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pdf_extractor import extract_first_pages
from llm_parser import parse_report

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"
LIST_PATH = DATA_DIR / "report_list.json"
OUTPUT_PATH = DATA_DIR / "parsed_reports.json"

FIRM_CODE_MAP = {
    "sinhan": "신한투자증권",
    "shinhan": "신한투자증권",
    "mirae": "미래에셋증권",
    "eugene": "유진투자증권",
}

FOLDER_CODE_MAP = {
    "samsung": "005930",
    "skhynix": "000660",
    "naver": "035420",
}

TYPE_MAP = {
    "er": "earnings_review",
    "en": "event_note",
    "cr": "company_report",
    "ep": "earnings_preview",
}


def parse_date(date_str: str) -> tuple[str, str]:
    """날짜 문자열 → (date_short, date_long)
    8자리 20250930 → ("25.09.30", "2025.09.30")
    6자리 250930   → ("25.09.30", "2025.09.30")
    """
    if len(date_str) == 8:
        return (
            f"{date_str[2:4]}.{date_str[4:6]}.{date_str[6:]}",
            f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}",
        )
    if len(date_str) == 6:
        return (
            f"{date_str[:2]}.{date_str[2:4]}.{date_str[4:]}",
            f"20{date_str[:2]}.{date_str[2:4]}.{date_str[4:]}",
        )
    return date_str, date_str


def parse_filename(pdf_path: Path) -> dict:
    """파일명에서 증권사, 날짜, 유형 파싱"""
    parts = pdf_path.stem.split("_")
    firm_code = parts[0]
    date_str = parts[1]
    type_code = parts[2] if len(parts) > 2 else "cr"
    date_short, date_long = parse_date(date_str)

    return {
        "firm": FIRM_CODE_MAP.get(firm_code, firm_code),
        "date_short": date_short,
        "date_long": date_long,
        "report_type": TYPE_MAP.get(type_code, "company_report"),
        "stock_code": FOLDER_CODE_MAP.get(pdf_path.parent.name, ""),
    }


def find_matched_report(report_list: list[dict], meta: dict) -> dict | None:
    """report_list.json에서 매칭 항목 찾기"""
    for report in report_list:
        if report["firm"] != meta["firm"]:
            continue
        if report["stock_code"] != meta["stock_code"]:
            continue
        if report["date"] in (meta["date_short"], meta["date_long"]):
            return report
    return None


def run(extract_only: bool = False, incremental: bool = False) -> list[dict]:
    with open(LIST_PATH, encoding="utf-8") as f:
        report_list = json.load(f)

    # 증분 모드: 기존 결과 로드 후 processed=True인 파일만 스킵
    existing: list[dict] = []
    existing_files: set[str] = set()
    if incremental and OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            existing = json.load(f)
        # processed=True인 항목만 완료로 취급, False이면 재파싱 대상
        done = [r for r in existing if r.get("processed")]
        existing_files = {r["pdf_file"] for r in done}
        existing = done  # 미완료 항목은 버리고 재파싱
        print(f"기존 완료 {len(existing)}건 유지, 신규/미완료 파일만 파싱\n")

    pdf_files = sorted(REPORTS_DIR.glob("*/*.pdf"))
    new_files = [
        p for p in pdf_files
        if str(p.relative_to(ROOT_DIR)).replace("\\", "/") not in existing_files
    ]

    print(f"전체 {len(pdf_files)}개 중 신규 {len(new_files)}개 파싱 시작\n")

    new_results = []
    for pdf_path in new_files:
        print(f"[파싱] {pdf_path.parent.name}/{pdf_path.name}")

        meta = parse_filename(pdf_path)
        text = extract_first_pages(pdf_path, n=3)

        if not text.strip():
            print("  [경고] 텍스트 추출 실패 → 스킵")
            continue

        if extract_only:
            parsed = {
                "target_price": None,
                "opinion": "unknown",
                "key_rationale": "",
            }
            print("  [extract-only] LLM 파싱 스킵")
        else:
            parsed = parse_report(text)
            print(f"  목표주가: {parsed.get('target_price')}")
            print(f"  투자의견: {parsed.get('opinion')}")
            rationale = parsed.get("key_rationale", "")
            print(f"  핵심근거: {rationale[:80]}{'...' if len(rationale) > 80 else ''}")

        matched = find_matched_report(report_list, meta)

        new_results.append({
            "pdf_file": str(pdf_path.relative_to(ROOT_DIR)).replace("\\", "/"),
            "firm": meta["firm"],
            "stock_code": meta["stock_code"],
            "date": meta["date_long"],
            "report_type": meta["report_type"],
            "title": matched["title"] if matched else "",
            "pdf_url": matched["pdf_url"] if matched else "",
            "target_price": parsed.get("target_price"),
            "opinion": parsed.get("opinion", "unknown"),
            "key_rationale": parsed.get("key_rationale", ""),
            "raw_text_preview": text[:500],
            "processed": not extract_only,
        })

    all_results = existing + new_results
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 신규 {len(new_results)}건 추가 (총 {len(all_results)}건)")
    print(f"저장: {OUTPUT_PATH}")
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF 리포트 파싱")
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="텍스트 추출만 수행 (OPENAI_API_KEY 없을 때 테스트용)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="기존 parsed_reports.json 유지하고 신규 파일만 추가",
    )
    args = parser.parse_args()
    run(extract_only=args.extract_only, incremental=args.incremental)
