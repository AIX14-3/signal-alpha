-- 020_dart_chunks.sql
-- ============================================================================
-- DART 공시 본문 임베딩 적재 — 결정론 전처리(임베딩) 단계 (worker 게이트형 파이프라인)
-- ----------------------------------------------------------------------------
-- 배경
--   architecture.mermaid의 "소스별 전처리(규칙·임베딩·통계지표)" 단계. DART 공시는
--   규칙추출(이벤트 유형·재무수치)에 더해 본문을 BGE-M3(1024d)로 임베딩해 pgvector에
--   적재한다. 생성형 LLM은 쓰지 않는다 — 임베딩은 고정 가중치라 결정론적이며, 동일
--   입력 → 동일 벡터라 백테스트·메타러너 학습과 정합한다(docs/design/worker-redesign.md).
--
-- 설계
--   - report_chunks(001_baseline)와 동형: (raw_document_id, chunk_index) UNIQUE로 멱등 적재.
--   - raw_documents(id, stock_id) 복합 FK + ON DELETE CASCADE로 원문 삭제 시 자동 정리.
--   - ivfflat(vector_cosine_ops) 인덱스: 적재·검색 모두 정규화 벡터 코사인(<=>) 기준.
--   - 임베딩 차원은 app/embeddings/provider.py EMBEDDING_DIM(1024)과 반드시 일치.
-- ============================================================================

CREATE TABLE dart_chunks (
    id BIGSERIAL PRIMARY KEY,
    raw_document_id BIGINT NOT NULL,
    stock_id BIGINT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    token_count INTEGER,
    embedding VECTOR(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dart_chunk UNIQUE (raw_document_id, chunk_index),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_dart_chunks_stock
    ON dart_chunks (stock_id);

CREATE INDEX idx_dart_chunks_embedding
    ON dart_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
