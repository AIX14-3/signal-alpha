-- 012_drop_sec_filings.sql
-- ============================================================================
-- 해외(미국) SEC EDGAR 공시 수집 제거 — sec_filings 테이블 폐기
-- ----------------------------------------------------------------------------
-- 002_sec_filings.sql / 003_sec_filings_updated_at_trigger.sql(PR #128)로 만든
-- SEC EDGAR 수집 기능을 제품에서 제거한다. 적용된 마이그레이션은 freeze 상태
-- (수정 금지·sha256 checksum)이므로 002/003을 in-place로 되돌리지 않고, 앞으로
-- 가는 마이그레이션으로 테이블을 DROP 한다.
--
-- DROP TABLE이 의존 인덱스(idx_sec_filings_*)와 트리거(trg_sec_filings_updated_at)를
-- 함께 제거한다. update_updated_at() 함수는 001_baseline.sql에서 정의되어 다른
-- 11개 테이블이 공유하므로 절대 건드리지 않는다. sec_filings를 참조하는 FK는 없다.
-- ============================================================================

DROP TABLE IF EXISTS sec_filings CASCADE;
