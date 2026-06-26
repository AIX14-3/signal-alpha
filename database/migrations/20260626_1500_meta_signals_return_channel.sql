-- 20260626_1500_meta_signals_return_channel.sql
-- target: collection
-- ============================================================================
-- meta_signals return 채널 컬럼 (#525 WS-C, D4)
-- ----------------------------------------------------------------------------
-- 배경
--   메타러너에 vol 채널(combined_vol)과 분리된 **return 채널**(final_score/direction/
--   confidence)을 추가한다. RETURN_COMBINE 태스크가 run_key='SRC'의 소스 base 예측(src_*)
--   + Report 피처를 combine_return 으로 결합해 이 컬럼에 적재한다. vol 채널은 불변(D4).
-- 설계
--   - confidence/method/model_count/weight_breakdown 은 vol 채널과 공유(재사용).
--   - return 행은 run_key='SRC' 로 분리 적재 — vol 행(run_key='ML')과 자연키로 공존.
--   - method 에 'linear_stacking'(부호 있는 선형 stacker) 추가. 'equal_fallback'/'empty' 공유.
--   - 멱등: ADD COLUMN IF NOT EXISTS + CHECK 제약 drop/add(DO 블록).
-- ============================================================================

ALTER TABLE meta_signals
    ADD COLUMN IF NOT EXISTS final_score DOUBLE PRECISION,   -- return 채널 결합 점수; 결합 불가 시 NULL
    ADD COLUMN IF NOT EXISTS direction VARCHAR(16);          -- positive|negative|neutral|unknown

-- method CHECK 확장: 'linear_stacking' 허용(인라인 제약 자동명 meta_signals_method_check).
DO $$
BEGIN
    ALTER TABLE meta_signals DROP CONSTRAINT IF EXISTS meta_signals_method_check;
    ALTER TABLE meta_signals
        ADD CONSTRAINT meta_signals_method_check
        CHECK (method IN ('stacking', 'equal_fallback', 'empty', 'linear_stacking'));
END $$;

-- direction 도메인 가드(NULL 허용 — vol 행은 direction 없음).
DO $$
BEGIN
    ALTER TABLE meta_signals DROP CONSTRAINT IF EXISTS meta_signals_direction_check;
    ALTER TABLE meta_signals
        ADD CONSTRAINT meta_signals_direction_check
        CHECK (direction IS NULL OR direction IN ('positive', 'negative', 'neutral', 'unknown'));
END $$;
