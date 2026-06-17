"""
parsed_reports.json → PDF 전체 텍스트 재추출 → 청킹 → BGE-M3 임베딩 → pgvector 적재
"""
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[6]
load_dotenv(ROOT_DIR / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from chunker import chunk_text  # noqa: E402
from pdf_extractor import extract_text  # noqa: E402

DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/signal_alpha")


def get_embedding_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-m3")


def embed_and_store(
    parsed_path: Path | str | None = None,
    dry_run: bool = False,
    incremental: bool = False,
):
    """
    Args:
        parsed_path: parsed_reports.json 경로 (None이면 기본값 사용)
        dry_run: True이면 DB 적재 없이 청크 수만 출력
        incremental: True이면 DB에 이미 있는 firm+date+stock_code는 스킵
    """
    if parsed_path is None:
        parsed_path = ROOT_DIR / "data" / "parsed_reports.json"

    with open(parsed_path, encoding="utf-8") as f:
        reports = json.load(f)

    # 증분 모드: DB에서 이미 적재된 (firm, date, stock_code) 조합 조회
    already_stored: set[tuple] = set()
    if incremental and not dry_run:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT firm, date, stock_code FROM report_chunks")
        already_stored = {row for row in cur.fetchall()}
        conn.close()
        print(f"DB 기존 {len(already_stored)}건 스킵 예정\n")

    if dry_run:
        print("[dry-run] DB 적재 없이 청크 수 확인\n")
        total = 0
        for report in reports:
            pdf_file = ROOT_DIR / report["pdf_file"]
            text = extract_text(pdf_file)
            chunks = chunk_text(text)
            print(f"  [{report['firm']}] {report['date']} {report['stock_code']} → {len(chunks)}청크")
            total += len(chunks)
        print(f"\n예상 총 청크 수: {total}")
        return

    import psycopg2
    model = get_embedding_model()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    total_chunks = 0

    for report in reports:
        key = (report["firm"], report["date"], report["stock_code"])
        if key in already_stored:
            print(f"[스킵] {report['firm']} {report['date']} {report['stock_code']} (이미 적재됨)")
            continue
        pdf_file = ROOT_DIR / report["pdf_file"]
        text = extract_text(pdf_file)
        chunks = chunk_text(text)
        print(f"[{report['firm']}] {report['date']} {report['stock_code']} → {len(chunks)}청크 임베딩 중...")

        embeddings = model.encode(chunks, normalize_embeddings=True, show_progress_bar=False)

        # report_raw: 리포트 메타데이터 1행 저장 (중복 시 스킵)
        cur.execute(
            """
            INSERT INTO report_raw
                (stock_code, firm, date, report_type, title, pdf_url,
                 target_price, opinion, key_rationale, raw_text_preview, processed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
            ON CONFLICT (firm, date, stock_code) DO NOTHING
            """,
            (
                report["stock_code"],
                report["firm"],
                report["date"],
                report.get("report_type", ""),
                report.get("title", ""),
                report.get("pdf_url", ""),
                report.get("target_price"),
                report.get("opinion", "unknown"),
                report.get("key_rationale", ""),
                report.get("raw_text_preview", ""),
            ),
        )

        for chunk, emb in zip(chunks, embeddings):
            cur.execute(
                """
                INSERT INTO report_chunks
                    (stock_code, firm, date, report_type, title, pdf_url,
                     target_price, opinion, key_rationale, chunk_text, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    report["stock_code"],
                    report["firm"],
                    report["date"],
                    report["report_type"],
                    report.get("title", ""),
                    report.get("pdf_url", ""),
                    report.get("target_price"),
                    report.get("opinion", "unknown"),
                    report.get("key_rationale", ""),
                    chunk,
                    emb.tolist(),
                ),
            )
            total_chunks += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n적재 완료: {total_chunks}청크 저장")


def backfill_report_raw(parsed_path: Path | str | None = None):
    """기존 parsed_reports.json → report_raw 테이블 백필 (임베딩 없이 메타데이터만)"""
    if parsed_path is None:
        parsed_path = ROOT_DIR / "data" / "parsed_reports.json"

    with open(parsed_path, encoding="utf-8") as f:
        reports = json.load(f)

    import psycopg2
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    inserted = 0

    for report in reports:
        cur.execute(
            """
            INSERT INTO report_raw
                (stock_code, firm, date, report_type, title, pdf_url,
                 target_price, opinion, key_rationale, raw_text_preview, processed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (firm, date, stock_code) DO NOTHING
            """,
            (
                report["stock_code"],
                report["firm"],
                report["date"],
                report.get("report_type", ""),
                report.get("title", ""),
                report.get("pdf_url", ""),
                report.get("target_price"),
                report.get("opinion", "unknown"),
                report.get("key_rationale", ""),
                report.get("raw_text_preview", ""),
                bool(report.get("processed", False)),
            ),
        )
        if cur.rowcount:
            inserted += 1
            print(f"  [insert] {report['firm']} {report['date']} {report['stock_code']}")
        else:
            print(f"  [skip]   {report['firm']} {report['date']} {report['stock_code']} (이미 있음)")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n백필 완료: {inserted}건 신규 저장")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="pgvector 임베딩 적재")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB 적재 없이 청크 수만 확인",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="DB에 이미 있는 리포트는 스킵하고 신규만 적재",
    )
    parser.add_argument(
        "--backfill-raw",
        action="store_true",
        help="기존 parsed_reports.json → report_raw 테이블 백필 (임베딩 없이)",
    )
    args = parser.parse_args()

    if args.backfill_raw:
        backfill_report_raw()
    else:
        embed_and_store(dry_run=args.dry_run, incremental=args.incremental)
