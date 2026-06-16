"""
main.py
Hiring Collector 실행 진입점

실행:
    DATABASE_URL=postgresql://... python services/agent-worker/app/collectors/hiring/main.py

  로컬 개발 환경 (.env 파일 사용):
    .env 파일에 DATABASE_URL=... 을 설정하거나 .env.example 참고

메뉴:
    1  Mock 수집기 (Fixture 기반)           - 테스트용
    2  Web Crawler (사람인 + 잡코리아)      - 포털 2개 수집, 공식 사이트 제외
    3  Multi-Source Crawler (전체 소스)     - 사람인 + 잡코리아 + 공식 사이트
    4  [Admin] DataLab 키워드 그룹 미리보기 - DB 실시간 연동, API 파라미터 즉시 검증
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """
    DATABASE_URL 환경변수 조회. .env 가 있으면 python-dotenv 로 자동 로드.

    설정 방법:
      1) .env 파일에 DATABASE_URL=postgresql://... 추가  (.env.example 참고)
      2) 또는 실행 시 환경변수 직접 지정:
         DATABASE_URL=postgresql://... python main.py

    Raises:
        RuntimeError: DATABASE_URL 이 설정되지 않은 경우
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv 미설치 시 환경변수만 사용

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL 환경변수가 설정되지 않았습니다.\n"
            ".env 파일에 DATABASE_URL=postgresql://... 을 추가하세요. (.env.example 참고)"
        )

    safe = db_url.split("@")[-1] if "@" in db_url else db_url
    logger.info("✓ DB 대상: ...@%s", safe)
    return db_url


def _preview_keyword_groups(db_url: str) -> None:
    """
    Mode 4: DB 실시간 조회 기반 키워드 그룹 미리보기.
    stocks 테이블의 name, sector, short_name 을 읽어 HiringKeywordGenerator 에 주입.
    """
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(db_url, echo=False, future=True)
    companies: list[dict] = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    "SELECT name, COALESCE(sector, 'tech'), short_name "
                    "FROM stocks WHERE is_target = TRUE ORDER BY name"
                )
            ).fetchall()
        companies = [
            {"company_name": name, "category": sector, "short_name": short_name}
            for name, sector, short_name in rows
        ]
    except Exception as exc:
        logger.error("❌ 미리보기용 DB 조회 실패: %s", exc)
        return
    finally:
        engine.dispose()

    if not companies:
        print("\n⚠️  현재 DB에 is_target=TRUE 기업이 없습니다.")
        print("   016_seed_stocks_targets.sql 과 015_stocks_is_target.sql 을 실행하세요.")
        return

    gen = HiringKeywordGenerator()
    groups = gen.generate_for_multiple_companies(companies)

    sep = "-" * 70
    print(f"\n{sep}")
    print(f"  DataLab 키워드 그룹 미리보기  [실시간 DB 연동]  (총 {len(groups)}개 기업)")
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
    print("  {")
    print(f'    "groupName": "{first["groupName"]}",')
    print(f'    "keywords": {first["keywords"]}')
    print("  }")
    print()


def main() -> None:
    print("\n" + "=" * 70)
    print("🔄 Signal α · Hiring Collector")
    print("=" * 70)
    print("  1  Mock (Fixture 기반)              - 테스트용")
    print("  2  Web Crawler (사람인+잡코리아)    - 포털 2개, 공식 사이트 제외")
    print("  3  Multi-Source (전체 소스)         - 사람인+잡코리아+공식 사이트")
    print("  4  [Admin] DataLab 키워드 그룹 미리보기 - DB 실시간 연동")
    print("  0  종료")

    choice = input("\n선택 (0-4): ").strip()
    if choice == "0":
        print("\n👋 종료합니다.\n")
        return

    # 모든 모드 공통: DB URL 취득 (Mode 4도 DB 실시간 조회)
    db_url = get_database_url()

    if choice == "4":
        _preview_keyword_groups(db_url)
        return

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
