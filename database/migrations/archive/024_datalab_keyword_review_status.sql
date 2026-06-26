-- 024_datalab_keyword_review_status.sql
-- DataLab 키워드 검색량 검증 관문(search-volume gate) 결과를 DB에 기록한다.
--
-- 배경: LLM(Gemini)이 생성한 DataLab 키워드는 사람 검수 전 할루시네이션(실제로 아무도
--   검색하지 않는 죽은 키워드)이 섞일 수 있다. 생성 직후 각 키워드를 네이버 DataLab의
--   실제 검색량으로 대조해 3단계로 분류한다:
--     auto  (검색일 충분)  → review_status='approved', is_active=TRUE  (즉시 수집 대상)
--     review(애매)         → review_status='pending',  is_active=FALSE (관리자 페이지 검수)
--     reject(검색량 거의 0)→ 적재하지 않음(드롭) / 수동으로 'rejected' 기록 가능
--
--   수집기(run_collectors.py)는 이미 is_active=TRUE만 사용하므로, pending(is_active=FALSE)
--   키워드는 수집에서 자동 제외된다. 관리자 페이지가 review_status='pending'을 조회해
--   승인(is_active=TRUE, review_status='approved')하면 다음 수집부터 포함된다.
--
--   (Scope: 점수 산식 불변. 키워드 라이프사이클 상태와 검증 근거 기록만 추가.)
--
-- 컬럼:
--   review_status           approved(승인·수집됨) / pending(보류·관리자 검수) / rejected(거부)
--   validation_active_days  검증 윈도 내 검색량>0 였던 '날' 수 (티어 산정 근거, 모델 비의존 신호)
--   validation_window_days  검증에 사용한 윈도 길이(일)
--   validation_coverage     active_days / window_days (0~1)
--   validated_at            검증 실행 시각
--
-- 트리거: datalab_category_keywords에는 trg_datalab_category_keywords_updated_at가
--   이미 부착돼 있어 추가 트리거 불필요. 컬럼만 ADD 한다.

ALTER TABLE datalab_category_keywords
    ADD COLUMN review_status VARCHAR(10) NOT NULL DEFAULT 'approved'
        CONSTRAINT chk_datalab_keyword_review_status
        CHECK (review_status IN ('approved', 'pending', 'rejected')),
    ADD COLUMN validation_active_days INTEGER
        CONSTRAINT chk_datalab_keyword_validation_active_days
        CHECK (validation_active_days IS NULL OR validation_active_days >= 0),
    ADD COLUMN validation_window_days INTEGER
        CONSTRAINT chk_datalab_keyword_validation_window_days
        CHECK (validation_window_days IS NULL OR validation_window_days > 0),
    ADD COLUMN validation_coverage NUMERIC(4,3)
        CONSTRAINT chk_datalab_keyword_validation_coverage
        CHECK (validation_coverage IS NULL OR validation_coverage BETWEEN 0 AND 1),
    ADD COLUMN validated_at TIMESTAMPTZ;

-- 관리자 페이지가 보류 키워드를 빠르게 조회하기 위한 부분 인덱스.
CREATE INDEX idx_datalab_category_keywords_pending
    ON datalab_category_keywords (category_id)
    WHERE review_status = 'pending';
