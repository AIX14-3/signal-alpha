-- 021_dart_document_features.sql
-- ============================================================================
-- DART 공시 임베딩 파생 결정론 피처 — 문서 단위 (worker 게이트형 파이프라인)
-- ----------------------------------------------------------------------------
-- 배경
--   020_dart_chunks가 적재한 공시 임베딩(BGE-M3, 1024d)을 모델이 쓰려면 1024차원 raw가
--   아니라 소수의 스칼라 피처로 환원해야 한다(과적합 방지 — docs/design/worker-redesign.md,
--   meta-learner-training.md). 이 테이블은 그 결정론 피처를 문서 단위로 적재한다.
--
-- 설계
--   - raw_document_id PK → 문서당 1행, 재임베딩 시 upsert(멱등).
--   - mean_prior_distance: 현재 공시 중심벡터 ↔ 같은 종목의 선적재 공시 청크들의
--     평균 코사인거리(pgvector `<=>`). "이 공시가 평소와 얼마나 다른가"(신규성).
--     과거 공시가 없으면 NULL(insufficient_history).
--   - prior_chunk_count: 거리 계산에 쓰인 과거 청크 수(표본 가드 입력).
--   - 임베딩은 결정론이라 같은 입력·같은 적재순서 → 같은 피처. 백테스트 정합.
-- ============================================================================

CREATE TABLE dart_document_features (
    raw_document_id BIGINT PRIMARY KEY,
    stock_id BIGINT NOT NULL,
    mean_prior_distance DOUBLE PRECISION,
    prior_chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (raw_document_id, stock_id)
        REFERENCES raw_documents(id, stock_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_dart_doc_features_stock
    ON dart_document_features (stock_id);
