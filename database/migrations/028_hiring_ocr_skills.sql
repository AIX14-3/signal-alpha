-- 028_hiring_ocr_skills.sql
-- 채용 공고 포스터 이미지에서 OCR 로 추출한 기술 스택을 hiring_raw_details 에 캐시한다.
--
-- 배경: 한국 공채 자격요건/우대사항은 대부분 포스터 이미지로 게시돼 content 텍스트가 거의
--   없다(#375). ENRICH_HIRING 도구가 extra_payload.image_urls 의 포스터를 Tesseract(kor+eng)
--   로 읽어 기술키워드 집합을 뽑아 여기 캐시하고, Hiring analyzer 는 캐시를 read 만 한다.
--   엔진 선정 근거: docs/archive/research/375-ocr-model-comparison.md §4(Tesseract — 최고 F1·최속).
--
--   patent_raw_details.llm_features/llm_status(019) 와 동형 패턴이다(별도 enrichment 도구가
--   채우는 보강 캐시 + pending 부분 인덱스 worklist). 적용된 기존 파일은 수정하지 않는다.
--
-- 컬럼:
--   ocr_skills  추출된 canonical 기술키워드 배열(JSONB). skipped/실패 시 NULL.
--   ocr_status  pending(미처리) / success(추출 완료) / failed(OCR·다운로드 실패) /
--               skipped(이미지 URL 없음)
--
--   (Scope: 점수 산식 불변. enrichment 캐시 컬럼만 추가.)

ALTER TABLE hiring_raw_details
    ADD COLUMN ocr_skills JSONB,
    ADD COLUMN ocr_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CONSTRAINT chk_hiring_ocr_status
        CHECK (ocr_status IN ('pending', 'success', 'failed', 'skipped'));

-- enrichment worklist (pending subset only — 완료되면 비어가는 부분 인덱스).
CREATE INDEX idx_hiring_ocr_pending
    ON hiring_raw_details (raw_document_id DESC)
    WHERE ocr_status = 'pending';
