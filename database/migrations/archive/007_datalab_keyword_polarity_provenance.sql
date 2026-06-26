-- 005_datalab_keyword_polarity_provenance.sql
-- DataLab polarity 분류 출처(provenance) 기록 컬럼 추가.
--
-- 배경: datalab_category_keywords.polarity(018)는 누가·얼마나 확신하고 분류했는지
--   흔적이 없다. LLM 분류 레인(services/agent-worker/datalab_polarity_keywords.py,
--   Stage 1)이 polarity를 채울 때 그 출처/신뢰도/모델/근거를 함께 남겨, 분석기가
--   agent_results.llm_model로 "이 신호 산출에 LLM이 관여했는지"를 전파할 수 있게 한다.
--   (Scope A: 점수 산식은 바뀌지 않는다. provenance 기록·전파만 추가.)
--
-- 컬럼:
--   polarity_source        'manual'(사람)·'llm'(분류기)·'default'(미태깅 폴백)
--   polarity_confidence    LLM 신뢰도 0~1 (manual/default는 NULL = 확정 취급)
--   polarity_model         분류에 쓴 LLM 모델명 → agent_results.llm_model로 전파
--   polarity_rationale     분류 근거(감사용, 투자 판단 문구 금지)
--   polarity_classified_at 분류 시각
--
-- 트리거: datalab_category_keywords에는 trg_datalab_category_keywords_updated_at가
--   012 컨벤션대로 이미 부착돼 있으므로 추가 트리거는 불필요하다. 컬럼만 ADD 한다.

ALTER TABLE datalab_category_keywords
    ADD COLUMN polarity_source VARCHAR(10) NOT NULL DEFAULT 'manual'
        CONSTRAINT chk_datalab_keyword_polarity_source
        CHECK (polarity_source IN ('manual', 'llm', 'default')),
    ADD COLUMN polarity_confidence NUMERIC(4,3)
        CONSTRAINT chk_datalab_keyword_polarity_confidence
        CHECK (polarity_confidence IS NULL OR polarity_confidence BETWEEN 0 AND 1),
    ADD COLUMN polarity_model VARCHAR(50),
    ADD COLUMN polarity_rationale TEXT,
    ADD COLUMN polarity_classified_at TIMESTAMPTZ;
