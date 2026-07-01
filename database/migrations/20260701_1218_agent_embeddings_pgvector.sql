-- 20260701_1218_agent_embeddings_pgvector.sql
-- target: collection
-- ============================================================================
-- 에이전트화 Stage 0 — 임베딩 인프라(0B): pgvector 확장 + RAG/에피소드 메모리 테이블
-- ----------------------------------------------------------------------------
-- 적용 대상 DB 에 pgvector(확장 `vector`)가 사용 가능해야 한다. 활성 대상 = Neon(pgvector 기본
-- 지원, `CREATE EXTENSION vector` 로 바로 활성). Cloud SQL 등 일부 관리형은 콘솔에서 확장을 먼저
-- 허용해야 CREATE EXTENSION 이 성공한다.
--
-- 배경
--   7-에이전트화 기획의 Stage 0. 임베딩을 "인프라"로만 깔아 둔다(에이전트 배선은 Stage 1/2).
--   임베딩 차원 = 768(팀 확정). RAG 청크(report_chunks)와 에피소드 메모리(signal_episodes)가
--   같은 768 차원을 공용한다.
--
-- 설계
--   - report_chunks: 증권사 리포트 본문을 청크 단위로 임베딩(RAG 검색용). report_raw_details
--     의 파생 상세이므로 원본 삭제 시 동반 삭제(FK ON DELETE CASCADE, README §3 raw-detail 규칙).
--     report_raw_details 의 PK 는 raw_document_id(detail 테이블 컨벤션)라 FK 도 그 컬럼을 참조한다.
--   - signal_episodes: 종목·일자·run_key 단위 시그널 발화 1건의 에피소드 메모리. 어느 소스가
--     어떤 방향/점수로 발화했는지(sources JSONB)를 임베딩과 함께 보관하고, 성패(outcome)는
--     나중에 채운다(NULL 시작). 이력 테이블이므로 stock 삭제 시 보존(FK 기본 NO ACTION, README §3).
--   - HNSW + vector_cosine_ops 인덱스로 코사인 유사도 근접검색(ANN)을 건다.
--   - target: collection — report_raw_details 는 수집 DB 전용, stocks 는 PUBLISHED(양 DB)라
--     수집 DB 에서 두 FK 모두 유효하다. 에피소드 메모리는 워커 내부 산출물이라 수집 DB 에 둔다.
--   - 롤(signal_worker) 존재 시에만 GRANT(0006 과 동일 패턴, 부분 적용/롤 선생성 안전).
-- 주: plain CREATE TABLE(IF NOT EXISTS 금지 — README §3). updated_at 컬럼 없음 → 트리거 불필요.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- report_chunks — 증권사 리포트 청크 임베딩(RAG)
-- ---------------------------------------------------------------------------
CREATE TABLE report_chunks (
    id                   BIGSERIAL PRIMARY KEY,
    report_raw_detail_id BIGINT NOT NULL
        REFERENCES report_raw_details(raw_document_id) ON DELETE CASCADE,
    chunk_index          INT NOT NULL,
    chunk_text           TEXT NOT NULL,
    embedding            vector(768) NOT NULL,
    token_count          INT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_report_chunks_detail_chunk UNIQUE (report_raw_detail_id, chunk_index)
);

CREATE INDEX idx_report_chunks_embedding
    ON report_chunks USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- signal_episodes — 시그널 발화 에피소드 메모리
-- ---------------------------------------------------------------------------
CREATE TABLE signal_episodes (
    id          BIGSERIAL PRIMARY KEY,
    stock_id    BIGINT NOT NULL REFERENCES stocks(id),
    signal_date DATE NOT NULL,
    run_key     TEXT NOT NULL,
    direction   TEXT,
    score       DOUBLE PRECISION,
    sources     JSONB,
    embedding   vector(768) NOT NULL,
    outcome     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_signal_episodes_stock_date_run UNIQUE (stock_id, signal_date, run_key)
);

CREATE INDEX idx_signal_episodes_embedding
    ON signal_episodes USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- 권한: signal_worker(수집 DB 소유 롤) — 롤 존재 시에만(0006 패턴)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON report_chunks, signal_episodes TO signal_worker;
        GRANT USAGE, SELECT ON SEQUENCE report_chunks_id_seq, signal_episodes_id_seq TO signal_worker;
    END IF;
END $$;
