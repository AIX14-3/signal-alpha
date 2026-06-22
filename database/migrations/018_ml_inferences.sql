-- 018_ml_inferences.sql
-- ============================================================================
-- ML/DL 추론 결과 적재 — vol-benchmark 변동성 모델 (worker 게이트형 파이프라인)
-- ----------------------------------------------------------------------------
-- 배경
--   architecture.mermaid의 "ML/DL 추론 (게이트 통과 모델만)" 단계. ML_INFER 태스크가
--   종목별 OHLCV(ohlcv_data)를 DataContract로 변환해 백테스트 채택(게이트 통과) 모델만
--   추론하고, 그 신호 피처(pred_vol 등)를 여기에 적재한다. 후속 메타러너 결합(PR3)이
--   이 행들을 소스 점수와 함께 stacking 입력으로 읽는다.
--
-- 설계
--   - 모델 1개 × horizon × asof 당 1행. pred_value는 실패 시 NULL이고 error_message에 사유.
--     (게이트 통과했더라도 데이터 부족/백엔드 오류로 개별 추론이 실패할 수 있어 원장에 남긴다.)
--   - gate_passed: 추론을 수행한 모델은 게이트 통과분뿐이라 항상 TRUE지만, 향후 거부 모델까지
--     기록할 수 있도록 컬럼으로 둔다.
--   - device: 'cpu' | 'gpu'. GPU 모델은 가용성 게이트로 호스트에 백엔드가 있을 때만 행이 생긴다.
--   - 멱등: (stock_id, run_key, asof_date, model_name, horizon) 자연키 UNIQUE → 재실행 upsert.
--     run_key는 stock_id가 NULL이 아닌 종목 단위 추론이라 stock_id NOT NULL.
-- ============================================================================

CREATE TABLE ml_inferences (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    run_key VARCHAR(50) NOT NULL DEFAULT 'ML',
    asof_date DATE NOT NULL,
    model_name VARCHAR(50) NOT NULL,
    horizon SMALLINT NOT NULL,
    pred_value DOUBLE PRECISION,                -- 예측 변동성(pred_vol); 추론 실패 시 NULL
    device VARCHAR(10) NOT NULL DEFAULT 'cpu'
        CHECK (device IN ('cpu', 'gpu')),
    gate_passed BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ml_inference UNIQUE (stock_id, run_key, asof_date, model_name, horizon)
);

CREATE INDEX idx_ml_inferences_stock_asof
    ON ml_inferences (stock_id, asof_date DESC);
