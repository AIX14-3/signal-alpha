CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON report_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
