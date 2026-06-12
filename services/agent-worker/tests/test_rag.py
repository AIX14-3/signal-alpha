"""
RAG 검색 테스트
사용법: python test_rag.py
        python test_rag.py --query "SK하이닉스 HBM 전망" --stock 000660
"""
import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/signal_alpha")


def search(query: str, stock_code: str | None = None, top_k: int = 5) -> list[tuple]:
    """쿼리와 가장 관련 있는 청크 검색"""
    import psycopg2
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("BAAI/bge-m3")
    q_emb = model.encode([query], normalize_embeddings=True)[0].tolist()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    if stock_code:
        cur.execute(
            """
            SELECT firm, date, report_type, title,
                   chunk_text, target_price, opinion,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM report_chunks
            WHERE stock_code = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (q_emb, stock_code, q_emb, top_k),
        )
    else:
        cur.execute(
            """
            SELECT firm, date, report_type, title,
                   chunk_text, target_price, opinion,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM report_chunks
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (q_emb, q_emb, top_k),
        )

    rows = cur.fetchall()
    conn.close()
    return rows


def print_results(query: str, rows: list[tuple]):
    print(f'\n{"=" * 60}')
    print(f"쿼리: {query}")
    print(f"결과 {len(rows)}건")
    print("=" * 60)
    for i, row in enumerate(rows, 1):
        firm, date, rtype, title, chunk, price, opinion, sim = row
        print(f"\n[{i}] {firm} | {date} | {rtype}")
        print(f"     제목: {title}")
        print(f"     목표주가: {price:,}원" if price else "     목표주가: -")
        print(f"     투자의견: {opinion} | 유사도: {sim:.3f}")
        print(f"     {chunk[:200].strip()}...")


DEFAULT_QUERIES = [
    ("삼성전자 하반기 목표주가", "005930"),
    ("SK하이닉스 HBM 전망", "000660"),
    ("네이버 AI 수익화 근거", "035420"),
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 검색 테스트")
    parser.add_argument("--query", "-q", help="검색 쿼리")
    parser.add_argument("--stock", "-s", help="종목코드 필터 (예: 005930)")
    parser.add_argument("--top-k", "-k", type=int, default=5, help="결과 수 (기본 5)")
    args = parser.parse_args()

    if args.query:
        rows = search(args.query, stock_code=args.stock, top_k=args.top_k)
        print_results(args.query, rows)
    else:
        print("기본 쿼리 3개 테스트 실행\n")
        for query, stock in DEFAULT_QUERIES:
            rows = search(query, stock_code=stock)
            print_results(query, rows)
