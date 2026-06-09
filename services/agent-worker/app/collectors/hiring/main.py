"""
main.py
Hiring Collector 실행 진입점

실행 (signal-alpha 루트에서):
    python services/agent-worker/app/collectors/main.py

  또는 환경변수 지정:
    DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha \\
    python services/agent-worker/app/collectors/main.py

메뉴:
    1  Mock 수집기 (Fixture 기반)           - 테스트용, DB 없이도 동작 확인
    2  Web Crawler (사람인 단독)            - 사람인만 수집
    3  Multi-Source Crawler (전체 소스)     - 사람인 + 잡코리아 + 15개 공식 사이트
    0  종료
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ── 형제 모듈 직접 import 가능하도록 sys.path 보강 ──────────────────────────
_COLLECTORS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_COLLECTORS_DIR))

from mock_collector import MockCollector          # noqa: E402
from web_crawler import WebCrawler                # noqa: E402
from multi_source_crawler import MultiSourceCrawler  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_DEFAULT_DATABASE_URL = (
    "postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
)

# 대상 기업 15개 (COMPANY_STOCK_MAP 과 동일)
_TARGET_COMPANIES = [
    "삼성전자", "SK하이닉스", "한미반도체",
    "NAVER", "카카오", "크래프톤",
    "현대자동차", "기아", "HL만도",
    "HYBE", "SM엔터테인먼트", "스튜디오드래곤",
    "삼성바이오로직스", "셀트리온", "유한양행",
]


def get_database_url() -> str:
    """DATABASE_URL 조회. .env 가 있으면 python-dotenv 로 로드(선택)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv 미설치 시 환경변수만 사용

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        db_url = _DEFAULT_DATABASE_URL
        logger.warning("⚠️  DATABASE_URL 미설정 → 기본값 사용")

    safe = db_url.split("@")[-1] if "@" in db_url else db_url
    logger.info("✓ DB 대상: ...@%s", safe)
    return db_url


def main() -> None:
    print("\n" + "=" * 70)
    print("🔄 Signal α · Hiring Collector")
    print("=" * 70)
    print("  1  Mock (Fixture 기반)          - 테스트용")
    print("  2  Web Crawler (사람인 단독)    - 단일 포털")
    print("  3  Multi-Source (전체 소스)     - 사람인+잡코리아+공식 15개 사이트")
    print("  0  종료")

    choice = input("\n선택 (0-3): ").strip()
    if choice == "0":
        print("\n👋 종료합니다.\n")
        return

    db_url = get_database_url()

    if choice == "1":
        print("\n🔄 Mock Collector 실행 중...")
        collector = MockCollector(database_url=db_url)
        count = collector.run()
        print(f"\n✅ Mock 수집 완료: 신규 {count}개 적재\n")

    elif choice == "2":
        print("\n🕷️  Web Crawler (사람인) 실행 중...  (이용약관/robots.txt 준수)")
        crawler = WebCrawler(
            database_url=db_url,
            target_companies=_TARGET_COMPANIES,
            headless=True,   # 디버깅 시 False
        )
        count = crawler.run()
        print(f"\n✅ 사람인 크롤링 완료: 신규 {count}개 적재\n")

    elif choice == "3":
        print("\n🌐 Multi-Source Crawler 실행 중...")
        print("   소스: 사람인 + 잡코리아 + 15개 공식 사이트")
        print("   ⚠️  이용약관/robots.txt 준수  |  완료까지 수 분 소요\n")

        # 세부 옵션: 포털 / 공식 사이트 선택
        use_portals = _ask_yn("  - 포털 수집 포함? (사람인+잡코리아) [Y/n]: ", default=True)
        use_official = _ask_yn("  - 공식 사이트 수집 포함? [Y/n]: ", default=True)

        crawler = MultiSourceCrawler(
            database_url=db_url,
            target_companies=_TARGET_COMPANIES,
            headless=True,
            use_portals=use_portals,
            use_official=use_official,
        )
        count = crawler.run()
        print(f"\n✅ 멀티소스 크롤링 완료: 신규 {count}개 적재\n")

    else:
        print("\n❌ 잘못된 선택입니다.\n")
        sys.exit(1)


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Y/n 입력 처리. Enter 는 default 값."""
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if ans in ("n", "no"):
        return False
    if ans in ("y", "yes", ""):
        return True
    return default


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  사용자 중단.\n")
        sys.exit(130)
    except Exception as exc:
        logger.error("❌ 실행 실패: %s", exc)
        sys.exit(1)
