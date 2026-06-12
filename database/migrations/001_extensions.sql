-- 001_extensions.sql
-- PostgreSQL 확장. pgvector는 report_chunks.embedding(VECTOR)에 필요하다.

CREATE EXTENSION IF NOT EXISTS vector;
