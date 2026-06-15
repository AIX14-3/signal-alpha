-- 017_hiring_signals_add_calculation_phase.sql
-- hiring_signals 테이블에 분석 근거 Phase 컬럼 추가
-- A: 14일 이동평균 기반, B: 네이버 DataLab 기반, C: 기본값 기반
ALTER TABLE hiring_signals
    ADD COLUMN IF NOT EXISTS calculation_phase VARCHAR(1);

COMMENT ON COLUMN hiring_signals.calculation_phase IS
    'A=14일 이동평균, B=DataLab 검색량, C=기본값(1.0) — 분석 근거 추적용';
