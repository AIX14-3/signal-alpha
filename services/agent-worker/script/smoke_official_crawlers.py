"""공식 채용관 크롤러 라이브 스모크 (#175 최종 검증).

머지된 8개 공식 채용관 파서가 **실제 사이트**에서 동작하는지 1회 실크롤로 확인한다.
픽스처 단위테스트로는 못 잡는 실 DOM drift / anti-bot / 타임아웃을 잡는 것이 목적.

- DB 미접촉(crawl()만 호출, 저장 안 함).
- Selenium 6사(현대/기아/SK하이닉스/크래프톤/HYBE/SM)는 싱글 headless 드라이버를 lazy 생성·재사용·
  마지막 1회 quit(diagnose_official_sites.py와 동일 패턴). naver/samsung은 requests 경로라 driver 불필요.
- 각 crawl()은 개별 try/except로 감싸 한 사이트 실패가 전체를 끊지 않게 한다.
- 사이트 간 짧은 휴식(time.sleep)으로 연속 타격에 의한 차단을 완화한다.

실행:
    uv run script/smoke_official_crawlers.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.collectors.hiring.sites.company.hyundai_kia import HyundaiCrawler, KiaCrawler
from app.collectors.hiring.sites.company.hybe_sm import HybeCrawler, SMCrawler
from app.collectors.hiring.sites.company.krafton import KraftonCrawler
from app.collectors.hiring.sites.company.naver import NaverCrawler
from app.collectors.hiring.sites.company.samsung import SamsungCrawler
from app.collectors.hiring.sites.company.sk_hynix import SKHynixCrawler

# (label, CrawlerClass, company_name, needs_selenium)
SITES = [
    ("HYUNDAI", HyundaiCrawler, "현대자동차", True),
    ("KIA", KiaCrawler, "기아", True),
    ("SK_HYNIX", SKHynixCrawler, "SK하이닉스", True),
    ("KRAFTON", KraftonCrawler, "크래프톤", True),
    ("HYBE", HybeCrawler, "HYBE", True),
    ("SM", SMCrawler, "SM엔터테인먼트", True),
    ("NAVER", NaverCrawler, "네이버", False),       # requests SSR
    ("SAMSUNG", SamsungCrawler, "삼성전자", False),  # guidance-only(requests)
]

_REST_SEC = 2  # 사이트 간 휴식


def _record(item) -> dict:
    """CollectorResult / dict 양쪽에서 표시용 필드 추출."""
    data = getattr(item, "data", item)
    return {
        "title": data.get("job_title"),
        "url": data.get("source_url") or data.get("job_link"),
    }


def main() -> None:
    print(f"라이브 스모크 시작 (8 사이트) → {PROJECT_ROOT}")
    print(f"{'LABEL':<12}{'STATUS':>8}{'COUNT':>7}{'SEC':>7}  SAMPLE / ERROR")
    print("-" * 90)

    driver = None
    results = []
    try:
        for label, cls, company, needs_selenium in SITES:
            if needs_selenium and driver is None:
                from app.collectors.hiring.driver_utils import create_chrome_driver
                driver = create_chrome_driver(headless=True)  # lazy 1회 생성

            crawler = cls(driver=driver if needs_selenium else None)
            t0 = time.monotonic()
            try:
                jobs = crawler.crawl(company) or []
                elapsed = time.monotonic() - t0
                status = "OK" if jobs else "EMPTY"
                sample = "; ".join(
                    f"{r['title']}" for r in (_record(j) for j in jobs[:2])
                )
                results.append((label, status, len(jobs), elapsed))
                print(f"{label:<12}{status:>8}{len(jobs):>7}{elapsed:>7.1f}  {sample[:60]}")
                if jobs:
                    for r in (_record(j) for j in jobs[:2]):
                        print(f"{'':<34}└ {r['url']}")
            except Exception as exc:  # 한 사이트 실패가 전체를 끊지 않게
                elapsed = time.monotonic() - t0
                results.append((label, "ERROR", 0, elapsed))
                print(f"{label:<12}{'ERROR':>8}{0:>7}{elapsed:>7.1f}  {type(exc).__name__}: {str(exc)[:60]}")

            time.sleep(_REST_SEC)  # 연속 타격 완화
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    print("-" * 90)
    ok = sum(1 for _, s, *_ in results if s == "OK")
    empty = sum(1 for _, s, *_ in results if s == "EMPTY")
    err = sum(1 for _, s, *_ in results if s == "ERROR")
    print(f"요약: OK={ok}  EMPTY={empty}  ERROR={err}  (총 {len(results)})")
    print("판정: EMPTY는 비수기 정상 가능(기아/SK하이닉스). ERROR/예상외 0은 라이브 drift 조사 대상.")


if __name__ == "__main__":
    main()
