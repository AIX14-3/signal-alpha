-- 019_meta_signals.sql
-- ============================================================================
-- 메타러너 결합 결과 적재 — vol-benchmark 모델 stacking (worker 게이트형 파이프라인)
-- ----------------------------------------------------------------------------
-- 배경
--   architecture.mermaid의 "메타러너 결합 stacking (학습된 가중)" 단계. META_COMBINE 태스크가
--   ml_inferences(모델별 pred_vol)를 학습된 가중(또는 균등 폴백)으로 결합해 종목/asof/horizon당
--   하나의 결합 변동성 + 신뢰도를 만든다. 끝단 LLM(PR5)이 이 수치를 설명에 쓰되 바꾸지 않는다.
--
-- 설계
--   - (stock_id, run_key, asof_date, horizon) 자연키 UNIQUE → 재실행 upsert(멱등).
--   - method: 'stacking'(학습 가중 적용) | 'equal_fallback'(가중 산출물 부재) | 'empty'(가용 추론 0).
--   - confidence: 모델 합의도 기반 [0,1]. weight_breakdown: 재정규화된 모델별 가중(JSONB).
--   - combined_vol은 결합 실패(추론 0건) 시 NULL.
-- ============================================================================

CREATE TABLE meta_signals (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    run_key VARCHAR(50) NOT NULL DEFAULT 'ML',
    asof_date DATE NOT NULL,
    horizon SMALLINT NOT NULL,
    combined_vol DOUBLE PRECISION,              -- stacking 결합 변동성; 결합 불가 시 NULL
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    method VARCHAR(20) NOT NULL DEFAULT 'stacking'
        CHECK (method IN ('stacking', 'equal_fallback', 'empty')),
    model_count SMALLINT NOT NULL DEFAULT 0,
    weight_breakdown JSONB,                     -- {model_name: weight} (재정규화)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_meta_signal UNIQUE (stock_id, run_key, asof_date, horizon)
);

CREATE INDEX idx_meta_signals_stock_asof
    ON meta_signals (stock_id, asof_date DESC);
