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
import time
from datetime import datetime
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
    via: str = "calendar",
    only_company: str | None = None,
    start_id: int | None,
    max_requests: int | None,
    pause: float,
    detail_pause: float = 0.2,
    batch_size: int,
    dry_run: bool,
) -> None:
    # 종목: --company 로 단일 지정 시 그것만, 아니면 DB is_target 전체(하드코딩 아님).
    names = [only_company] if only_company else get_target_companies(db_url)
    if not names:
        raise SystemExit("수집 대상 종목이 없습니다(--company 또는 DB is_target 확인).")
    matches = _target_matcher(names)
    crawler = J.JasoseolCrawler(driver=None)
    sink = None if dry_run else _Sink(db_url)

    batch: list[dict] = []
    # generous=관대 사전필터 통과, kept=레코드 빌드, inserted=_resolve_stock 통과 실적재.
    # detail_ok/detail_fallback=포스터 보유 vs 폴백(포스터 없음) — OCR 백필 도달 범위 측정.
    generous = kept = inserted = detail_ok = detail_fallback = 0

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

    def add(name: str, detail: dict, posting_date: str | None) -> None:
        nonlocal kept
        rec = crawler._to_record(name, name, detail, posting_date=posting_date)
        if not rec:
            return
        # backfill 핵심: observed_date 를 '오늘'이 아니라 실제 게시일(KST)로 주입한다.
        # 이게 없으면 과거(2020~) 공고가 전부 '오늘 관측'으로 쌓여 과거 시계열이 소실되고
        # 오늘 신호가 오염된다. base_collector 가 KST 날짜로 정규화(_to_kst_date),
        # 게시일 누락 시에만 _kst_today() 로 폴백. (라이브 수집은 override 없음 → 무영향)
        if rec.get("posting_date"):
            rec["observed_date"] = rec["posting_date"]
        batch.append(rec)
        kept += 1
        if len(batch) >= batch_size:
            flush()

    J.reset_cache()

    if via == "calendar":
        until_eff = until or datetime.now().strftime("%Y-%m-%d")
        logger.info("calendar backfill: %s ~ %s (월별 열거 → 매칭분만 상세 fetch)", since, until_eff)
        for posting in J.iter_calendar_postings(since, until_eff, pause_sec=pause):
            name = posting.get("name") or ""
            if not matches(name):
                continue
            generous += 1
            # 매칭분만 상세 fetch → 포스터 image_urls. 마감/오류면 목록 폴백(포스터 없는 레코드).
            detail = J._fetch_detail(posting["id"])
            if detail:
                detail_ok += 1
            else:
                detail_fallback += 1
                detail = posting
            add(name, detail, J._detail_date(detail) or posting.get("start_time"))
            if detail_pause:
                time.sleep(detail_pause)
        flush()
        logger.info("=" * 60)
        logger.info(
            "calendar backfill 완료: 관대매칭 generous=%d → 실적재 inserted=%d "
            "(레코드 kept=%d, detail_ok=%d / fallback=%d, since=%s until=%s dry_run=%s)",
            generous, inserted, kept, detail_ok, detail_fallback, since, until_eff, dry_run,
        )
        logger.warning("주의: generous 는 관대 사전필터 통과 수(과대) — 실제 종목별 수는 DB 쿼리(_resolve_stock 통과)가 진실.")
        return

    # via == "idscan": 개별 상세 id-scan(레거시 폴백 경로).
    max_id = None
    if until:
        max_id = max(0, J.find_boundary_id(until) - 1)
        logger.info("until=%s → 스캔 상한 id=%d", until, max_id)
    scanned = 0
    for pid, detail in J.iter_backfill_details(
        since, start_id=start_id, max_id=max_id, pause_sec=pause, max_requests=max_requests
    ):
        scanned += 1
        name = detail.get("name") or ""
        if not matches(name):
            continue
        generous += 1
        add(name, detail, J._detail_date(detail))
        if scanned % 2000 == 0:
            logger.info("진행: scanned=%d generous=%d (마지막 id=%d)", scanned, generous, pid)
    flush()
    logger.info("=" * 60)
    logger.info("idscan backfill 완료: 스캔 %d · 관대매칭 %d · 적재 %d (since=%s, dry_run=%s)",
                scanned, generous, inserted, since, dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="자소설닷컴 과거 공고 깊은 backfill")
    ap.add_argument("--via", choices=["calendar", "idscan"], default="calendar",
                    help="수집 경로(기본 calendar=기간열거; idscan=레거시 id 순회 폴백)")
    ap.add_argument("--since", default=_DEFAULT_SINCE, help=f"수집 시작일(YYYY-MM-DD, 기본 {_DEFAULT_SINCE})")
    ap.add_argument("--until", default=None, help="수집 종료일(YYYY-MM-DD) — calendar 는 미지정 시 오늘")
    ap.add_argument("--company", default=None, help="단일 종목명만 수집(미지정 시 DB is_target 전체)")
    ap.add_argument("--start-id", type=int, default=None, help="[idscan] 재개용 시작 id(미지정 시 경계 자동탐색)")
    ap.add_argument("--max-requests", type=int, default=None, help="[idscan] 이번 실행 최대 상세요청 수")
    ap.add_argument("--pause", type=float, default=0.3, help="목록/스캔 요청 간 대기초(기본 0.3)")
    ap.add_argument("--detail-pause", type=float, default=0.2, help="[calendar] 매칭분 상세 fetch 간 대기초")
    ap.add_argument("--batch-size", type=int, default=200, help="적재 배치 크기")
    ap.add_argument("--dry-run", action="store_true", help="적재 없이 수집만")
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url and not args.dry_run:
        raise SystemExit("DATABASE_URL 환경변수가 필요합니다(--dry-run 은 예외).")

    run_backfill(
        db_url, args.since,
        until=args.until, via=args.via, only_company=args.company,
        start_id=args.start_id, max_requests=args.max_requests,
        pause=args.pause, detail_pause=args.detail_pause,
        batch_size=args.batch_size, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
