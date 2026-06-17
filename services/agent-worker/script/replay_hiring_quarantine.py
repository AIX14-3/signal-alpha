# scripts/replay_hiring_quarantine.py
"""hiring_quarantine 격리 레코드 재적재(replay) 유틸 (Phase 4).

3c 게이트/_insert_one 오류로 failed 거부된 크롤 레코드는 hiring_quarantine 에 격리된다.
코드 수정(예: stocks 신규 등록, 일시 DB 오류 해소) 후 이 스크립트로 재적재한다.

모드:
  --list          미replay 격리 현황(사유/사이트별 카운트)만 출력.
  (기본) replay-data : 미replay 레코드의 record_payload(parse된 dict)를 다시
                       insert_to_db 에 투입 → 새 collector_run 에서 재적재 시도.
                       투입한 격리 레코드는 replayed_at/replayed_run_id 로 멱등 마킹.

replay-reparse(raw_payload 를 수정된 파서로 재파싱)는 원본 보존 사이트가 생긴 뒤
후속 PR(4b)에서 구현한다 — 현재 legacy 크롤러는 raw_payload 가 전부 NULL 이다.

DATABASE_URL(.env) 단일 소스를 사용한다.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT.parent.parent / ".env")


def _dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL 이 필요합니다(.env).")
    return dsn


def list_quarantine(engine) -> None:
    """미replay 격리 현황을 사유/사이트별로 요약 출력."""
    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM hiring_quarantine WHERE replayed_at IS NULL")
        ).scalar()
        rows = conn.execute(
            text("""
                SELECT source_label, violation_reason, COUNT(*) AS cnt,
                       COUNT(*) FILTER (WHERE raw_payload IS NOT NULL) AS with_raw
                FROM hiring_quarantine
                WHERE replayed_at IS NULL
                GROUP BY source_label, violation_reason
                ORDER BY cnt DESC
            """)
        ).fetchall()
    print(f"미replay 격리 레코드: {total}건")
    print("-" * 70)
    for r in rows:
        print(f"  {r.cnt:>4}건 | raw보존 {r.with_raw:>3} | {r.source_label or '-'} | {r.violation_reason}")
    if not rows:
        print("  (없음)")


def replay_data(engine, limit: int) -> None:
    """미replay 격리 레코드의 record_payload 를 재적재하고 replayed 로 마킹."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, record_payload
                FROM hiring_quarantine
                WHERE replayed_at IS NULL
                ORDER BY created_at ASC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()

    if not rows:
        print("재적재할 격리 레코드가 없습니다.")
        return

    payloads = [r.record_payload for r in rows]  # JSONB → dict
    ids = [r.id for r in rows]
    print(f"격리 레코드 {len(payloads)}건 재적재 시도...")

    # 구체 수집기의 insert_to_db 로 재적재(하이브리드: dict 그대로 수용).
    try:
        from app.collectors.hiring.multi_source_crawler import MultiSourceCrawler
    except ImportError:
        from multi_source_crawler import MultiSourceCrawler  # type: ignore
    crawler = MultiSourceCrawler(database_url=_dsn())
    inserted = crawler.insert_to_db(payloads)
    print(f"  → 신규 적재 {inserted}건")

    # 재적재 시도한 격리 레코드를 replayed 처리(직전 생성된 HIRING run 으로 기록, 멱등).
    with engine.begin() as conn:
        new_run_id = conn.execute(
            text("SELECT MAX(id) FROM collector_runs WHERE collector_type = :t"),
            {"t": "HIRING"},
        ).scalar()
        conn.execute(
            text("""
                UPDATE hiring_quarantine
                SET replayed_at = NOW(), replayed_run_id = :rid
                WHERE id = ANY(:ids) AND replayed_at IS NULL
            """),
            {"rid": new_run_id, "ids": ids},
        )
    print(f"  → 격리 {len(ids)}건 replayed 마킹(run_id={new_run_id})")


def replay_reparse(engine, limit: int) -> None:
    """raw_payload 를 수정된 파서로 재파싱해 재적재(replay-reparse, Phase 4b).

    selector 수정 후 사이트에 다시 요청하지 않고 격리된 원본 HTML 로 backfill 한다.
    source_label 로 파서를 매핑하며, 3가지 정합성 가드를 적용한다:
      ① 인스턴스의 source_label 을 명시 설정(crawl() 우회로 _make_record 가 참조하는 값 누락 방지)
      ② _fetch_soup 와 동일한 'html.parser' 엔진 사용(selector 일관성)
      ③ record_payload 의 company/url 복원 시 None fallback(박살난 레코드도 안전 진입)
    """
    from bs4 import BeautifulSoup

    try:
        from app.collectors.hiring.base_collector import CollectorResult
        from app.collectors.hiring.multi_source_crawler import MultiSourceCrawler
        from app.collectors.hiring.sites.company import SimpleSiteCrawler
    except ImportError:  # pragma: no cover
        from base_collector import CollectorResult  # type: ignore
        from multi_source_crawler import MultiSourceCrawler  # type: ignore
        from sites.company import SimpleSiteCrawler  # type: ignore

    # source_label → (크롤러 클래스, 파서 메서드명). 사이트 수리 시 점진 확장.
    reparse_parsers = {"SAMSUNG_BIO_CAREERS": (SimpleSiteCrawler, "_parse_samsung_bio")}

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, source_label, record_payload, raw_payload
                FROM hiring_quarantine
                WHERE raw_payload IS NOT NULL AND replayed_at IS NULL
                ORDER BY created_at ASC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()

    if not rows:
        print("reparse 대상(raw_payload 보존 + 미replay) 격리가 없습니다.")
        return

    pairs: list[tuple] = []
    done_ids: list[int] = []
    for row in rows:
        label = row.source_label
        if label not in reparse_parsers:
            print(f"  매핑 없는 source_label '{label}' → skip (id={row.id})")
            continue
        crawler_cls, method = reparse_parsers[label]
        crawler = crawler_cls()              # 가드①: 무인자 생성(driver=None)
        crawler.source_label = label         #          + source_label 명시 설정
        soup = BeautifulSoup(row.raw_payload, "html.parser")  # 가드②
        rec = row.record_payload or {}
        company = rec.get("company_name") or ""               # 가드③
        url = rec.get("source_url") or rec.get("job_link") or ""
        results = getattr(crawler, method)(company, url, soup, row.raw_payload)
        for r in results:
            data = r.data if isinstance(r, CollectorResult) else r
            raw = r.raw_payload if isinstance(r, CollectorResult) else None
            pairs.append((data, raw))
        done_ids.append(row.id)

    if not pairs:
        print("재파싱 결과가 없습니다(매핑/파서 결과 0).")
        return

    print(f"재파싱 {len(pairs)}건 재적재 시도(격리 {len(done_ids)}건 기반)...")
    inserted = MultiSourceCrawler(database_url=_dsn()).insert_to_db(pairs)
    print(f"  → 신규 적재 {inserted}건")

    with engine.begin() as conn:
        new_run_id = conn.execute(
            text("SELECT MAX(id) FROM collector_runs WHERE collector_type = :t"),
            {"t": "HIRING"},
        ).scalar()
        conn.execute(
            text("""
                UPDATE hiring_quarantine
                SET replayed_at = NOW(), replayed_run_id = :rid
                WHERE id = ANY(:ids) AND replayed_at IS NULL
            """),
            {"rid": new_run_id, "ids": done_ids},
        )
    print(f"  → 격리 {len(done_ids)}건 replayed 마킹(run_id={new_run_id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="hiring_quarantine replay 유틸")
    parser.add_argument("--list", action="store_true", help="미replay 격리 현황만 출력")
    parser.add_argument(
        "--reparse", action="store_true",
        help="raw_payload 를 수정된 파서로 재파싱해 재적재(replay-reparse)",
    )
    parser.add_argument("--limit", type=int, default=500, help="replay 최대 건수")
    args = parser.parse_args()

    engine = create_engine(_dsn(), echo=False, future=True)
    try:
        if args.list:
            list_quarantine(engine)
        elif args.reparse:
            replay_reparse(engine, args.limit)
        else:
            replay_data(engine, args.limit)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
