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
    2  Web Crawler (사람인 + 잡코리아)      - 포털 2개 수집, 공식 사이트 제외
    3  Multi-Source Crawler (전체 소스)     - 사람인 + 잡코리아 + 15개 공식 사이트
    4  [Admin] DataLab 키워드 그룹 미리보기 - DB 불필요, API 파라미터 즉시 검증
    0  종료
"""

from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path

# Windows cp949 콘솔에서 한글/이모지 깨짐 방지 — 프로세스 시작 직후 UTF-8로 교체
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 형제 모듈 직접 import 가능하도록 sys.path 보강 ──────────────────────────
_COLLECTORS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_COLLECTORS_DIR))

from keyword_generator import HiringKeywordGenerator  # noqa: E402
from mock_collector import MockCollector               # noqa: E402
from multi_source_crawler import MultiSourceCrawler   # noqa: E402
from web_crawler import WebCrawler                     # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_DEFAULT_DATABASE_URL = (
    "postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
)

# Mode 4 전용: DB 불필요, 기업 카테고리 정보만 사용
# 기업 목록 변경은 DB(is_target) 기준이 우선 — 이 dict는 카테고리 표시용으로만 사용
_COMPANY_CATEGORIES: dict[str, str] = {
    "삼성전자": "반도체", "SK하이닉스": "반도체", "한미반도체": "반도체장비",
    "NAVER": "인터넷", "카카오": "인터넷", "크래프톤": "게임",
    "현대자동차": "자동차", "기아": "자동차", "HL만도": "자동차부품",
    "HYBE": "엔터", "SM엔터테인먼트": "엔터", "스튜디오드래곤": "콘텐츠",
    "삼성바이오로직스": "바이오", "셀트리온": "바이오", "유한양행": "제약",
}


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


def _preview_keyword_groups() -> None:
    """Mode 4: HiringKeywordGenerator 결과를 터미널에 출력해 즉시 검증 (DB 불필요)."""
    gen = HiringKeywordGenerator()

    companies = [
        {"company_name": name, "category": _COMPANY_CATEGORIES.get(name, "기타")}
        for name in _COMPANY_CATEGORIES
    ]
    groups = gen.generate_for_multiple_companies(companies)

    sep = "-" * 70
    print(f"\n{sep}")
    print(f"  DataLab 키워드 그룹 미리보기  (총 {len(groups)}개 기업)")
    print(sep)

    total_keywords = 0
    for name, group in groups.items():
        kw_count = group["keyword_count"]
        total_keywords += kw_count
        print(f"\n  [{group['category']}] {group['groupName']}")
        print(f"  키워드 {kw_count}개:")
        for i, kw in enumerate(group["keywords"], 1):
            print(f"    {i}. {kw}")

    print(f"\n{sep}")
    print(f"  합계: {len(groups)}개 기업 / 총 {total_keywords}개 키워드")
    print(f"  네이버 DataLab API 1회 호출 배치(5개) 기준 → {-(-len(groups) // 5)}회 필요")
    print(sep)

    first_name = next(iter(groups))
    first = groups[first_name]
    print(f"\n  [Naver API 전송 형태 샘플 — {first_name}]")
    print(f"  {{")
    print(f'    "groupName": "{first["groupName"]}",')
    print(f'    "keywords": {first["keywords"]}')
    print(f"  }}")
    print()


def main() -> None:
    print("\n" + "=" * 70)
    print("🔄 Signal α · Hiring Collector")
    print("=" * 70)
    print("  1  Mock (Fixture 기반)              - 테스트용")
    print("  2  Web Crawler (사람인+잡코리아)    - 포털 2개, 공식 사이트 제외")
    print("  3  Multi-Source (전체 소스)         - 사람인+잡코리아+공식 15개 사이트")
    print("  4  [Admin] DataLab 키워드 그룹 미리보기 - DB 불필요, 즉시 검증")
    print("  0  종료")

    choice = input("\n선택 (0-4): ").strip()
    if choice == "0":
        print("\n👋 종료합니다.\n")
        return

    if choice == "4":
        # DB 연결 불필요 — 순수 로직만 실행
        _preview_keyword_groups()
        return

    # Mode 1~3 공통: DB URL 취득 (기업 목록은 run() 내부에서 자동 로드)
    db_url = get_database_url()

    if choice == "1":
        print("\n🔄 Mock Collector 실행 중...")
        collector = MockCollector(database_url=db_url)
        count = collector.run()
        print(f"\n✅ Mock 수집 완료: 신규 {count}개 적재\n")

    elif choice == "2":
        print("\n🕷️  Web Crawler (사람인 + 잡코리아)")
        print("   (이용약관/robots.txt 준수)")
        crawler = MultiSourceCrawler(
            database_url=db_url,
            headless=True,
            use_portals=True,
            use_official=False,
        )
        count = crawler.run()
        print(f"\n✅ 포털 크롤링 완료: 신규 {count}개 적재\n")

    elif choice == "3":
        print("\n🌐 Multi-Source Crawler")
        print("   소스: 사람인 + 잡코리아 + 공식 사이트")
        print("   ⚠️  이용약관/robots.txt 준수  |  완료까지 수 분 소요\n")

        use_portals = _ask_yn("  - 포털 수집 포함? (사람인+잡코리아) [Y/n]: ", default=True)
        use_official = _ask_yn("  - 공식 사이트 수집 포함? [Y/n]: ", default=True)

        crawler = MultiSourceCrawler(
            database_url=db_url,
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
