"""DB 초기화: pgvector extension + 전체 테이블 생성"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
import psycopg2

db_url = os.environ.get("DATABASE_URL", "")
print(f"DATABASE_URL: {db_url[:40]}...")

conn = psycopg2.connect(db_url)
cur = conn.cursor()

cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
print("pgvector extension OK")

# 리포트 원본 메타데이터 (1행 = 1 리포트)
cur.execute("""
CREATE TABLE IF NOT EXISTS report_raw (
    id                SERIAL PRIMARY KEY,
    stock_code        VARCHAR(10)  NOT NULL,
    firm              VARCHAR(50)  NOT NULL,
    date              VARCHAR(20)  NOT NULL,
    report_type       VARCHAR(30),
    title             TEXT,
    pdf_url           TEXT,
    target_price      INT,
    opinion           VARCHAR(20),
    key_rationale     TEXT,
    raw_text_preview  TEXT,
    processed         BOOLEAN      DEFAULT false,
    created_at        TIMESTAMP    DEFAULT NOW(),
    UNIQUE (firm, date, stock_code)
)
""")
cur.execute("CREATE INDEX IF NOT EXISTS report_raw_stock_idx ON report_raw (stock_code)")
print("report_raw 테이블 OK")

# 리포트 분석 결과 (Analyzer 출력 저장)
cur.execute("""
CREATE TABLE IF NOT EXISTS report_signal (
    id                SERIAL PRIMARY KEY,
    stock_code        VARCHAR(10)  NOT NULL,
    direction         VARCHAR(20),
    score             FLOAT,
    avg_target        FLOAT,
    upside_pct        FLOAT,
    target_trend      VARCHAR(20),
    conflict_detected BOOLEAN,
    opinions          JSONB,
    risk_flags        JSONB,
    summary           TEXT,
    data_status       VARCHAR(20),
    analyzed_at       TIMESTAMP    DEFAULT NOW()
)
""")
cur.execute("CREATE INDEX IF NOT EXISTS report_signal_stock_idx ON report_signal (stock_code)")
print("report_signal 테이블 OK")

# pgvector 청크 (RAG 검색용, 1행 = 1 청크)
cur.execute("""
CREATE TABLE IF NOT EXISTS report_chunks (
    id           SERIAL PRIMARY KEY,
    stock_code   VARCHAR(10)  NOT NULL,
    firm         VARCHAR(50)  NOT NULL,
    date         VARCHAR(20)  NOT NULL,
    report_type  VARCHAR(30)  NOT NULL,
    title        TEXT,
    pdf_url      TEXT,
    target_price INT,
    opinion      VARCHAR(20),
    key_rationale TEXT,
    chunk_text   TEXT         NOT NULL,
    embedding    vector(1024),
    created_at   TIMESTAMP    DEFAULT NOW()
)
""")
cur.execute(
    "CREATE INDEX IF NOT EXISTS report_chunks_emb_idx "
    "ON report_chunks USING ivfflat (embedding vector_cosine_ops)"
)
print("report_chunks 테이블 OK")

conn.commit()
cur.close()
conn.close()
print("\nDB 설정 완료!")
