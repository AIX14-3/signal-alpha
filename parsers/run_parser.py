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


def parse_filename(pdf_path: Path) -> dict:
    """파일명에서 증권사, 날짜, 유형 파싱"""
    parts = pdf_path.stem.split("_")
    firm_code = parts[0]
    date_str = parts[1]
    type_code = parts[2] if len(parts) > 2 else "cr"

    return {
        "firm": FIRM_CODE_MAP.get(firm_code, firm_code),
        "date_short": f"{date_str[2:4]}.{date_str[4:6]}.{date_str[6:]}",
        "date_long": f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}",
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


def run(extract_only: bool = False) -> list[dict]:
    with open(LIST_PATH, encoding="utf-8") as f:
        report_list = json.load(f)

    results = []
    pdf_files = sorted(REPORTS_DIR.glob("*/*.pdf"))

    print(f"PDF 파일 {len(pdf_files)}개 파싱 시작\n")

    for pdf_path in pdf_files:
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

        results.append({
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

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(results)}건 파싱")
    print(f"저장: {OUTPUT_PATH}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF 리포트 파싱")
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="텍스트 추출만 수행 (OPENAI_API_KEY 없을 때 테스트용)",
    )
    args = parser.parse_args()
    run(extract_only=args.extract_only)
