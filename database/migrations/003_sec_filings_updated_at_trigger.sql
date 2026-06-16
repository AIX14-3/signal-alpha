-- 003_sec_filings_updated_at_trigger.sql
-- ============================================================================
-- sec_filings.updated_at 자동 갱신 트리거 보강 (database/README §3 컨벤션)
-- ----------------------------------------------------------------------------
-- 002_sec_filings.sql이 updated_at 컬럼은 두었으나 trg_<table>_updated_at
-- 트리거를 누락했다. README §3은 "updated_at 컬럼이 있으면 trg_<table>_updated_at
-- 트리거를 함께 추가"를 의무화한다. 리포지토리 upsert는 updated_at = NOW()를
-- 직접 박아두지만, 그 외 UPDATE 경로(예: stock_id 백필)에서는 갱신되지 않는다.
--
-- 002는 이미 main(#128)에 릴리스되어 freeze 상태이므로(적용된 마이그레이션 수정
-- 금지·sha256 checksum), in-place 편집 대신 새 마이그레이션으로 트리거를 추가한다.
-- update_updated_at() 함수는 001_baseline.sql에서 이미 정의되어 11개 테이블이 공유.
-- ============================================================================

CREATE TRIGGER trg_sec_filings_updated_at
BEFORE UPDATE ON sec_filings
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();
