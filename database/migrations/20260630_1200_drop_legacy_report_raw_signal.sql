-- 20260630_1200_drop_legacy_report_raw_signal.sql
-- target: collection
-- ============================================================================
-- 레거시 report_raw / report_signal 테이블 제거
-- ----------------------------------------------------------------------------
-- 배경: report RAG MVP 가 마이그 체계 밖(setup_db.py)에서 만들어 쓰던 테이블. 현재 report
--   런타임은 canonical 경로(raw_documents → report_raw_details)로 이전돼 더 이상 참조하지
--   않는다(런타임 코드 .py/.ts 참조 0 확인, 2026-06-30). README §7 의 "추후 DROP" 계획 이행.
-- 설계: 두 테이블은 COLLECTION DB 전용이라 target: collection. 멱등 DROP(IF EXISTS), 소유
--   시퀀스는 테이블과 함께 제거된다. CASCADE 는 쓰지 않는다 — 의외의 의존이 있으면 조용히
--   지우지 말고 실패로 드러내기 위함. canonical 인 report_raw_details 와 혼동 금지(이 둘만 제거).
-- ============================================================================

DROP TABLE IF EXISTS public.report_signal;
DROP TABLE IF EXISTS public.report_raw;
