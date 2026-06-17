-- Migration 002: report_raw_details에 S3 파이프라인 컬럼 추가
-- 목적: PDF를 S3에 저장하고 LLM 파싱 결과를 DB에 직접 저장하는 자동화 파이프라인 지원

ALTER TABLE report_raw_details
    ADD COLUMN IF NOT EXISTS s3_key       VARCHAR(500),
    ADD COLUMN IF NOT EXISTS parsed_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS key_rationale TEXT;
