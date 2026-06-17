# script/diagnose_official_sites.py
"""공식 채용관 진단 툴킷 (#175 잔여 — '스카우터').

각 타겟 URL에 requests 요청을 날려 **응답 상태 + raw 바이트**를
`diagnostics/{LABEL}_RAW.{ext}` 로 일괄 덤프한다. 목적은 파서 수리가 아니라,
각 사이트의 **실상태 팩트체크**(공고 있음/없음/SPA/DNS 죽음)로 사이트별 운명
(selector 수정 / 안내 1건 / 비활성)을 정하는 것. 덤프 파일은 후속 파서 단위테스트
픽스처로 재사용한다.

설계 포인트:
  - URL은 실 크롤러 모듈에서 import(Single Source of Truth) — 진단 툴과 크롤러가
    같은 URL을 보게 한다.
  - raw 는 resp.content(bytes)를 'wb'로 보존 — EUC-KR/CP949 등 구형 인코딩 깨짐 방어
    (resp.text 디코딩 추측을 거치지 않고 원본 그대로 저장).
  - --selenium 은 SPA 의심 사이트의 렌더 DOM 을 덤프. 싱글 드라이버를 lazy 생성·재사용
    하고 마지막에 딱 한 번 quit(좀비 방지).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.collectors.hiring.sites.company.hyundai_kia import _HYUNDAI_LIST, _KIA_LIST
from app.collectors.hiring.sites.company.krafton import _JOBS as KRAFTON_JOBS
from app.collectors.hiring.sites.company.naver import _LIST_URL as NAVER_URL
from app.collectors.hiring.sites.company.sk_hynix import _LIST as SK_HYNIX_LIST
from app.collectors.hiring.user_agents import pick_ua

# (label, url, content_type, selenium_hint)
# 죽은 엔드포인트(SAMSUNG/HYBE/SM)는 사용자가 브라우저로 확인한 새 URL을 **직접 명시**한다
# (아직 크롤러 상수에 없어 import 불가). selector 검증 후 크롤러 상수에 반영하며 import(SSOT)로
# 되돌린다. SK_HYNIX 는 검증 완료되어 _LIST(SSOT) import 로 환원(#175/#233).
TARGETS: list[tuple[str, str, str, bool]] = [
    ("SAMSUNG_ELECTRONICS", "https://www.samsungcareers.com/hr/", "html", True),   # 끝 슬래시 필수(/hr는 404). 관계사 선택형 통합포털
    ("NAVER", f"{NAVER_URL}?pageNo=1", "html", True),
    ("SK_HYNIX", SK_HYNIX_LIST, "html", True),
    ("HYUNDAI", _HYUNDAI_LIST, "html", True),
    ("KIA", _KIA_LIST, "html", True),
    ("HYBE", "https://careers.hybecorp.com/ko/career", "html", True),             # /ko/jobs 404
    ("SM", "https://recruit.smentertainment.com/ko/sm-apply", "html", True),      # /ko/jobs 404
    ("KRAFTON", KRAFTON_JOBS, "html", True),
]

_OUTDIR = PROJECT_ROOT / "diagnostics"
_SPA_SUSPECT_BYTES = 2048  # static HTML 이 이보다 짧으면 SPA(빈 골격) 의심


def diagnose_requests(label: str, url: str, content_type: str, outdir: Path) -> dict:
    """requests 1회 진단. status + raw 바이트 저장(raise 안 함). 결과 요약 dict 반환."""
    try:
        resp = requests.get(url, headers={"User-Agent": pick_ua()}, timeout=10)
    except requests.exceptions.ConnectionError as exc:
        return {"label": label, "status": "DNS_FAIL", "bytes": 0,
                "encoding": "-", "note": str(exc)[:50]}
    except Exception as exc:  # noqa: BLE001 - 한 사이트 실패가 진단을 멈추지 않게
        return {"label": label, "status": "REQ_FAIL", "bytes": 0,
                "encoding": "-", "note": f"{type(exc).__name__}"}

    ext = "json" if content_type == "json" else "html"
    (outdir / f"{label}_RAW.{ext}").write_bytes(resp.content)  # 원본 바이트 보존
    note = "SPA 의심(static 골격만)" if len(resp.content) < _SPA_SUSPECT_BYTES else ""
    return {"label": label, "status": resp.status_code, "bytes": len(resp.content),
            "encoding": resp.encoding or "-", "note": note}


def diagnose_selenium(targets: list[tuple], outdir: Path) -> None:
    """SPA 의심 타겟의 렌더 DOM 덤프. 싱글 드라이버 lazy init + 마지막 1회 quit."""
    try:
        from app.collectors.hiring.driver_utils import create_chrome_driver
    except ImportError:  # pragma: no cover
        from driver_utils import create_chrome_driver  # type: ignore

    driver = None
    try:
        for label, url, _ctype, sel_hint in targets:
            if not sel_hint:
                continue
            if driver is None:
                driver = create_chrome_driver(headless=True)  # lazy 1회 생성
            try:
                driver.get(url)
                time.sleep(3)  # SPA 렌더 대기
                page = driver.page_source
                (outdir / f"{label}_RENDERED.html").write_text(page, encoding="utf-8")
                print(f"  [selenium] {label}: {len(page)} chars rendered")
            except Exception as exc:  # noqa: BLE001
                print(f"  [selenium] {label}: FAIL {type(exc).__name__}")
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="공식 채용관 진단 툴킷 (#175)")
    parser.add_argument("--selenium", action="store_true",
                        help="SPA 의심 사이트의 렌더 DOM도 덤프(싱글 드라이버 재사용)")
    parser.add_argument("--only", help="단일 LABEL만 진단")
    args = parser.parse_args()

    targets = TARGETS
    if args.only:
        targets = [t for t in TARGETS if t[0] == args.only]
        if not targets:
            raise SystemExit(
                f"알 수 없는 label: {args.only} (가능: {[t[0] for t in TARGETS]})"
            )

    _OUTDIR.mkdir(exist_ok=True)
    print(f"진단 시작 → {_OUTDIR}")
    print(f"{'LABEL':<22}{'STATUS':>10}{'BYTES':>9}{'ENC':>11}  NOTE")
    print("-" * 74)
    for label, url, content_type, _sel in targets:
        r = diagnose_requests(label, url, content_type, _OUTDIR)
        print(f"{r['label']:<22}{str(r['status']):>10}{r['bytes']:>9}"
              f"{r['encoding']:>11}  {r['note']}")

    if args.selenium:
        print("\n[--selenium] SPA 렌더 DOM 덤프:")
        diagnose_selenium(targets, _OUTDIR)

    print("\n완료. diagnostics/ 의 *_RAW.* (및 --selenium 시 *_RENDERED.html) 확인.")


if __name__ == "__main__":
    main()
