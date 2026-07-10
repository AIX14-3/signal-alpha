#!/usr/bin/env python
"""코스피 상위 200 회사 로고 PNG 를 stock_logos 테이블에 적재(upsert)한다.

전제:
  - 20260710_1000_stock_logos.sql 마이그레이션이 대상(워커/수집) DB 에 적용되어 있을 것.
  - assets/stock_logos/{ticker}.png + logos_manifest.csv 가 존재할 것.

매칭: 파일명(ticker) → stocks.ticker → stocks.id(stock_id) 조인. stocks 에 없는
종목(ETF 잔여·미등록 우선주 등)은 SKIP 하고 목록으로 보고한다(테이블은 그대로 유지).

연결: --database-url 또는 DATABASE_URL (수집/워커 DB DSN). migrate.py 와 동일 규칙.

사용:
    python database/tools/load_stock_logos.py                 # 실제 적재
    python database/tools/load_stock_logos.py --dry-run       # 매칭만 점검
    python database/tools/load_stock_logos.py --database-url postgres://...
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[2]
LOGO_DIR = ROOT / "assets" / "stock_logos"
MANIFEST = LOGO_DIR / "logos_manifest.csv"


def resolve_dsn(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    # 루트 .env 폴백(간단 파서)
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("DATABASE_URL 을 찾을 수 없습니다. --database-url 또는 환경변수로 지정하세요.")


def load_manifest() -> list[dict]:
    with open(MANIFEST, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    ap = argparse.ArgumentParser(description="stock_logos 적재")
    ap.add_argument("--database-url", dest="database_url", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = load_manifest()
    print(f"매니페스트 {len(rows)}건 로드 · 로고 디렉토리 {LOGO_DIR}")

    conn = psycopg2.connect(resolve_dsn(args.database_url))
    conn.autocommit = False
    inserted = updated = skipped_missing = 0
    missing: list[str] = []

    with conn, conn.cursor() as cur:
        # ticker → stock_id 매핑
        cur.execute("SELECT ticker, id FROM stocks")
        tick2id = {t: i for t, i in cur.fetchall()}

        for r in rows:
            code, name, source = r["code"], r["name"], r["source"]
            stock_id = tick2id.get(code)
            if stock_id is None:
                skipped_missing += 1
                missing.append(f"{code} {name}")
                continue
            png = (LOGO_DIR / f"{code}.png").read_bytes()
            if args.dry_run:
                continue
            cur.execute(
                """
                INSERT INTO stock_logos (stock_id, image, mime_type, source, updated_at)
                VALUES (%s, %s, 'image/png', %s, now())
                ON CONFLICT (stock_id) DO UPDATE
                    SET image = EXCLUDED.image,
                        mime_type = EXCLUDED.mime_type,
                        source = EXCLUDED.source,
                        updated_at = now()
                RETURNING (xmax = 0) AS did_insert
                """,
                (stock_id, psycopg2.Binary(png), source),
            )
            if cur.fetchone()[0]:
                inserted += 1
            else:
                updated += 1

        if args.dry_run:
            conn.rollback()

    matched = len(rows) - skipped_missing
    print(f"\n매칭됨 {matched} / {len(rows)}  (stocks 미등록 {skipped_missing}건 SKIP)")
    if not args.dry_run:
        print(f"  INSERT {inserted} · UPDATE {updated}")
    if missing:
        print("\nstocks 테이블에 없어 건너뛴 종목:")
        for m in missing:
            print("  -", m)


if __name__ == "__main__":
    main()
