"""자소설닷컴 과거 채용공고 깊은 backfill (id 범위 스캔 → DB 적재).

WHY
    회사별 이력 엔드포인트는 최근 ~2년만 준다(하드캡). 더 과거(기본 2020년)는
    개별 상세 GET /api/v1/employment_companies/{id} 로만 접근된다. id 는 시간순
    단조정수라, since 날짜의 경계 id 를 이진탐색으로 **라이브 발견**한 뒤 최신까지
    훑어 우리 종목의 과거 공고를 모은다.

하드코딩 금지(요구사항)
    - 종목: DB stocks(is_target) — get_target_companies()
    - since 날짜: --since 인자 또는 env HIRING_JASOSEOL_BACKFILL_SINCE (기본 2020-01-01)
    - 경계/최대 id: 라이브 발견(코드에 박힌 id 없음)

적재
    기존 BaseCollector.insert_to_db 재사용 → raw_documents/hiring_raw_details/
    processing_queue + source_hash dedup + 실제 게시일(published_at). 종목 매칭도
    insert 단계 _resolve_stock(stocks)가 최종 권위. 본 스크립트의 사전필터는 효율용.

주의(부하·예의)
    2020→현재는 약 6~7만 id 스캔이라 무거운 1회성 작업이다. pause/배치/재개(start-id)/
    max-requests 로 보수적으로 운영하라. 개인정보(자소서/지원자)는 수집하지 않는다.

USAGE
    # 소량 시범(요청 2000건만, 적재까지)
    uv run python scripts/backfill_jasoseol_history.py --max-requests 2000
    # 2020년부터 전체 (오래 걸림 — 재개 가능)
    uv run python scripts/backfill_jasoseol_history.py --since 2020-01-01
    # 중단됐으면 직전 로그의 start-id 로 재개
    uv run python scripts/backfill_jasoseol_history.py --start-id 81234
    # 적재 없이 수집만 점검
    uv run python scripts/backfill_jasoseol_history.py --max-requests 500 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# agent-worker 루트를 path 에 (app.* import)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.hiring.base_collector import BaseCollector, get_target_companies  # noqa: E402
from app.collectors.hiring.sites import jasoseol as J  # noqa: E402

logger = logging.getLogger("backfill_jasoseol")

_DEFAULT_SINCE = os.getenv("HIRING_JASOSEOL_BACKFILL_SINCE", "2020-01-01")


class _Sink(BaseCollector):
    """insert_to_db 재사용 전용 — collect/parse 는 쓰지 않음."""

    def collect(self, target_companies):  # pragma: no cover - 미사용
        return []

    def parse(self, raw_data):
        return raw_data


def _target_matcher(names: list[str]):
    """DB 종목명 → 정규화 집합. 공고 회사명이 (정확/부분) 매칭되면 True(사전필터, 관대)."""
    norm_targets = {J._norm(n) for n in names if n}
    norm_targets.discard("")

    def matches(company_name: str) -> bool:
        c = J._norm(company_name or "")
        if not c:
            return False
        for t in norm_targets:
            if c == t or (len(t) >= 3 and t in c) or (len(c) >= 3 and c in t):
                return True
        return False

    return matches


def run_backfill(
    db_url: str,
    since: str,
    *,
    until: str | None = None,
    only_company: str | None = None,
    start_id: int | None,
    max_requests: int | None,
    pause: float,
    batch_size: int,
    dry_run: bool,
) -> None:
    # 종목: --company 로 단일 지정 시 그것만, 아니면 DB is_target 전체(하드코딩 아님).
    names = [only_company] if only_company else get_target_companies(db_url)
    if not names:
        raise SystemExit("수집 대상 종목이 없습니다(--company 또는 DB is_target 확인).")
    # --until 지정 시 그 날짜 직전까지로 스캔 상한(연도 한정 backfill).
    max_id = None
    if until:
        max_id = max(0, J.find_boundary_id(until) - 1)
        logger.info("until=%s → 스캔 상한 id=%d", until, max_id)
    matches = _target_matcher(names)
    crawler = J.JasoseolCrawler(driver=None)
    sink = None if dry_run else _Sink(db_url)

    batch: list[dict] = []
    scanned = kept = inserted = 0

    def flush():
        nonlocal inserted, batch
        if not batch:
            return
        if dry_run:
            for r in batch:
                logger.info("  [dry] %s | %s | %s",
                            (r["posting_date"] or "")[:10], r["company_name"], r["job_title"][:40])
        else:
            inserted += sink.insert_to_db(list(batch))
        batch = []

    J.reset_cache()
    for pid, detail in J.iter_backfill_details(
        since, start_id=start_id, max_id=max_id, pause_sec=pause, max_requests=max_requests
    ):
        scanned += 1
        name = detail.get("name") or ""
        if not matches(name):
            continue
        rec = crawler._to_record(name, name, detail, posting_date=J._detail_date(detail))
        if rec:
            # backfill 핵심: observed_date 를 '오늘'이 아니라 실제 게시일(KST)로 주입한다.
            # 이게 없으면 과거(2020~) 공고가 전부 '오늘 관측'으로 쌓여 과거 시계열이 소실되고
            # 오늘 신호가 오염된다. base_collector 가 KST 날짜로 정규화(_to_kst_date),
            # 게시일 누락 시에만 _kst_today() 로 폴백. (라이브 수집은 override 없음 → 무영향)
            if rec.get("posting_date"):
                rec["observed_date"] = rec["posting_date"]
            batch.append(rec)
            kept += 1
            if len(batch) >= batch_size:
                logger.info("진행: scanned=%d kept=%d (마지막 id=%d) → 배치 적재", scanned, kept, pid)
                flush()
    flush()

    logger.info("=" * 60)
    logger.info("backfill 완료: 스캔 %d · 종목매칭 %d · 적재 %d (since=%s, dry_run=%s)",
                scanned, kept, inserted, since, dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="자소설닷컴 과거 공고 깊은 backfill")
    ap.add_argument("--since", default=_DEFAULT_SINCE, help=f"수집 시작일(YYYY-MM-DD, 기본 {_DEFAULT_SINCE})")
    ap.add_argument("--until", default=None, help="수집 종료일(YYYY-MM-DD, 미만까지) — 연도 한정용")
    ap.add_argument("--company", default=None, help="단일 종목명만 수집(미지정 시 DB is_target 전체)")
    ap.add_argument("--start-id", type=int, default=None, help="재개용 시작 id(미지정 시 경계 자동탐색)")
    ap.add_argument("--max-requests", type=int, default=None, help="이번 실행 최대 상세요청 수(시범/안전상한)")
    ap.add_argument("--pause", type=float, default=0.3, help="요청 간 대기초(기본 0.3, 예의 rate-limit)")
    ap.add_argument("--batch-size", type=int, default=200, help="적재 배치 크기")
    ap.add_argument("--dry-run", action="store_true", help="적재 없이 수집만")
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url and not args.dry_run:
        raise SystemExit("DATABASE_URL 환경변수가 필요합니다(--dry-run 은 예외).")

    run_backfill(
        db_url, args.since,
        until=args.until, only_company=args.company,
        start_id=args.start_id, max_requests=args.max_requests,
        pause=args.pause, batch_size=args.batch_size, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
