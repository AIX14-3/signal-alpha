-- 20260626_1530_final_signals_return_channel.sql
-- target: collection
-- ============================================================================
-- final_signals return 채널 노출 컬럼 (#525 WS-C, D4)
-- ----------------------------------------------------------------------------
-- 배경
--   메타러너 return 채널(meta_signals run_key='SRC' 의 final_score/direction/confidence)을
--   소비처(백엔드/프론트)가 읽도록 final_signals 에 미러링한다. api.signals_current 는
--   final_signals.* 를 노출하므로, 이 컬럼을 더하면 백엔드 read-model 변경 없이 자동 노출된다.
-- 설계
--   - 기존 결정론 집계 점수(final_score/signal/confidence)는 그대로 두고(0..100 스케일),
--     ML return 채널은 별도 ml_* 컬럼으로 병행(혼동/충돌 없음). 둘 다 NULL 허용(채널 결측 안전).
--   - RETURN_COMBINE 핸들러가 현재 발행 신호(is_current)에 이 컬럼을 patch 한다(narrative
--     오버레이와 동일 패턴). 미산출 종목은 NULL.
--   - ml_final_score 는 return 점수(부호 있는 실수, 0..100 아님) → NUMERIC 자유 스케일.
-- ============================================================================

ALTER TABLE final_signals
    ADD COLUMN IF NOT EXISTS ml_final_score DOUBLE PRECISION,   -- return 채널 점수(부호 있음); 결측 NULL
    ADD COLUMN IF NOT EXISTS ml_direction VARCHAR(16),          -- positive|negative|neutral|unknown
    ADD COLUMN IF NOT EXISTS ml_confidence DOUBLE PRECISION;    -- return 채널 방향 합의 신뢰도 [0,1]

DO $$
BEGIN
    ALTER TABLE final_signals DROP CONSTRAINT IF EXISTS chk_final_signal_ml_direction;
    ALTER TABLE final_signals
        ADD CONSTRAINT chk_final_signal_ml_direction
        CHECK (ml_direction IS NULL OR ml_direction IN ('positive', 'negative', 'neutral', 'unknown'));
END $$;
