-- 20260625_1227_recommendations.sql
-- ============================================================================
-- recommendations
-- ----------------------------------------------------------------------------
-- 배경: ML/DL/LLM 라인이 산출한 종목별 final_signal(5소스 종합 리포트) + meta_signal(ML 변동성)
--       을 결정론 "주목·관심 추천 점수"로 합쳐 랭킹한 결과를 적재한다. 추천은 투자 조언이 아니라
--       발행 신호의 확신·안정성 기준 랭킹이다(매수/매도 지시 아님).
-- 설계: 종목/asof/run_key 당 1행 멱등(upsert). recommendation_score = 100·dir_w·conf_w·vol_w
--       (dir_w=final_score/100|중립0.5, conf_w=신뢰도, vol_w=1/(1+penalty·combined_vol) 변동성 역가중).
--       basis 는 점수 근거('final'=발행 신호, 'meta'=ML 변동성만). components 에 가중 분해 저장.
-- ============================================================================

CREATE TABLE IF NOT EXISTS recommendations (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    asof_date DATE NOT NULL,
    run_key VARCHAR(30) NOT NULL DEFAULT 'REC',
    rank SMALLINT NOT NULL,
    recommendation_score NUMERIC(6,2) NOT NULL CHECK (recommendation_score BETWEEN 0 AND 100),
    basis VARCHAR(10) NOT NULL CHECK (basis IN ('final', 'meta')),
    signal VARCHAR(10) CHECK (signal IN ('positive', 'negative', 'neutral', 'mixed')),
    final_score NUMERIC(5,2),
    confidence NUMERIC(6,4),
    combined_vol DOUBLE PRECISION,
    components JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_recommendation UNIQUE (stock_id, asof_date, run_key)
);

CREATE INDEX IF NOT EXISTS idx_recommendations_rank
    ON recommendations (asof_date, run_key, rank);

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.
