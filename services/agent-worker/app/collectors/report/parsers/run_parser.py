"""
PDF 리포트 일괄 파싱
- PDF 전체 텍스트 추출 → parsed_reports.json 저장 (로컬 CLI 모드)
- report storage에서 PDF bytes 수신 → 전체 텍스트 규칙 기반 파싱, 선택적 LLM 보강 (자동화 파이프라인 모드)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.collectors.report.storage import ReportStorageClient

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from pdf_extractor import extract_text
from llm_parser import parse_report

ROOT_DIR = Path(__file__).resolve().parents[6]
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


def process_from_s3(
    storage_key: str,
    storage_client: ReportStorageClient,
    *,
    settings: object | None = None,
) -> dict:
    """Report storage에서 PDF bytes를 받아 전체 텍스트 기반 파싱 결과 반환."""
    pdf_bytes = storage_client.download_pdf(storage_key)
    text = extract_text(pdf_bytes)
    result = parse_report_deterministic(text)
    if bool(getattr(settings, "report_use_llm", False)):
        try:
            llm_config = _build_llm_config(settings)
            if llm_config is None:
                return {**result, "raw_text": text}
            result = _merge_parser_results(
                result,
                parse_report(
                    _llm_candidate_text(text),
                    llm_config=llm_config,
                ),
            )
        except Exception as exc:
            print(f"  [LLM 보강 오류] {exc}")
    result["raw_text"] = text
    return result


def parse_report_deterministic(text: str) -> dict:
    """LLM 없이 리포트 텍스트에서 정량 메타와 근거 후보를 추출한다."""
    return {
        "target_price": _extract_target_price(text),
        "opinion": _extract_opinion(text),
        "key_rationale": _extract_key_rationale(text),
    }


def _extract_target_price(text: str) -> int | None:
    patterns = [
        r"(?:목표\s*주가|목표주가|목표가|Target\s*Price|TP)(?:\s*\([^)]*\))?\D{0,80}([0-9][0-9,.]*)\s*(만원|원|KRW)?",
        r"([0-9][0-9,.]*)\s*(만원|원|KRW)?\D{0,30}(?:목표\s*주가|목표주가|목표가|Target\s*Price|TP)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return _parse_price(match.group(1), match.group(2) if len(match.groups()) > 1 else None)
    return None


def _extract_opinion(text: str) -> str:
    opinion_window = _window_around_keywords(text, ["투자의견", "Investment opinion", "Rating"], size=300)
    target = opinion_window or text[:3000]
    if re.search(
        r"\b(strong\s*buy|trading\s*buy|buy|outperform|overweight)\b|매수|강력매수",
        target,
        flags=re.IGNORECASE,
    ):
        return "buy"
    if re.search(
        r"\b(hold|neutral|market\s*perform|marketperform)\b|중립|보유",
        target,
        flags=re.IGNORECASE,
    ):
        return "neutral"
    if re.search(r"\b(sell|underperform|reduce|underweight)\b|매도", target, flags=re.IGNORECASE):
        return "sell"
    return "unknown"


def _parse_price(raw_number: str, unit: str | None) -> int | None:
    try:
        value = float(raw_number.replace(",", ""))
    except ValueError:
        return None

    multiplier = 10000 if unit == "만원" else 1
    return int(value * multiplier)


def _extract_key_rationale(text: str) -> str:
    keywords = (
        "근거",
        "실적",
        "전망",
        "업황",
        "수요",
        "공급",
        "리스크",
        "밸류에이션",
        "개선",
        "성장",
        "수익성",
    )
    lines = [_clean_line(line) for line in text.splitlines()]
    candidates = [
        line
        for line in lines
        if len(line) >= 12 and any(keyword in line for keyword in keywords)
    ]
    if not candidates:
        candidates = [line for line in lines if len(line) >= 20]
    return " ".join(candidates[:4])[:1000]


def _merge_parser_results(base: dict, supplement: dict) -> dict:
    return {
        "target_price": supplement.get("target_price") or base.get("target_price"),
        "opinion": (
            supplement.get("opinion")
            if supplement.get("opinion") and supplement.get("opinion") != "unknown"
            else base.get("opinion", "unknown")
        ),
        "key_rationale": supplement.get("key_rationale") or base.get("key_rationale", ""),
    }


def _llm_candidate_text(text: str) -> str:
    windows = [
        _window_around_keywords(text, ["목표주가", "목표 주가", "투자의견", "Target Price", "Rating"], size=1500),
        _window_around_keywords(text, ["투자포인트", "실적", "전망", "리스크", "밸류에이션"], size=2500),
    ]
    candidate = "\n\n".join(window for window in windows if window.strip())
    return candidate or text[:5000]


def _window_around_keywords(text: str, keywords: list[str], *, size: int) -> str:
    lower_text = text.lower()
    for keyword in keywords:
        index = lower_text.find(keyword.lower())
        if index >= 0:
            start = max(0, index - size // 2)
            end = min(len(text), index + size)
            return text[start:end]
    return ""


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class _ReportLlmConfig:
    client: object
    model: str
    timeout_seconds: float


def _build_llm_config(settings: object | None) -> object | None:
    """리포트 PDF 파싱 LLM 보강용 클라이언트 구성(목표가/의견 추출).

    report_use_llm=True이고 provider 키가 갖춰졌을 때만 클라이언트를 만든다.
    그 외에는 None을 반환해 규칙 기반 파싱만 사용한다.
    """
    if settings is None:
        return None
    if not bool(getattr(settings, "report_use_llm", False)):
        return None

    model = str(getattr(settings, "report_llm_model", "") or "").strip()
    if not model:
        return None

    provider = str(getattr(settings, "report_llm_provider", "gemini") or "gemini").strip().lower()
    timeout_seconds = float(getattr(settings, "report_llm_timeout_seconds", 20.0) or 20.0)

    from app.analyzers.dart.llm import GeminiGenerateContentClient, OpenAiChatClient
    from app.observability.langsmith import maybe_trace

    if provider == "gemini":
        api_key = str(getattr(settings, "gemini_api_key", "") or "").strip()
        if not api_key:
            return None
        base_url = str(getattr(settings, "gemini_base_url", "") or "").strip()
        client = GeminiGenerateContentClient(
            api_key=api_key,
            **({"base_url": base_url} if base_url else {}),
        )
    elif provider == "openai":
        api_key = str(getattr(settings, "openai_api_key", "") or "").strip()
        if not api_key:
            return None
        base_url = str(getattr(settings, "openai_base_url", "") or "").strip()
        client = OpenAiChatClient(
            api_key=api_key,
            **({"base_url": base_url} if base_url else {}),
        )
    else:
        return None

    return _ReportLlmConfig(
        client=maybe_trace(client, name="report_llm"),
        model=model,
        timeout_seconds=timeout_seconds,
    )


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
        text = extract_text(pdf_path)

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
