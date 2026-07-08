-- 20260708_1000_journal_retro_outcome_class.sql
-- target: backend
-- ============================================================================
-- 저널 회고 결과 분류 (경량 구조 회고)
-- ----------------------------------------------------------------------------
-- 배경: 회고(retrospective_memo)에 더해, outcome 확정 후 "계획 대비 결과"를 중립적으로
--   한 번에 분류할 수 있는 경량 구조 필드를 추가한다. 값: as_planned / unexpected_good /
--   unexpected_bad. 성과 판정이 아니라 학습을 위한 기록(사후확신/look-ahead 금지).
-- 주의: 과거 초기 설계의 decision_type/decision_reason(매매 결정 기록)은 018 정책으로
--   20260702_1401 에서 제거됐다. 이 컬럼은 그 이름을 되살리지 않고 목적 명명한 신규 컬럼이다.
-- 설계: 멱등 ADD(IF NOT EXISTS). 허용값 검증은 앱 레이어(_validate_retro_outcome)에서 한다.
-- ============================================================================

ALTER TABLE public.signal_journals
    ADD COLUMN IF NOT EXISTS retro_outcome_class character varying(20);
