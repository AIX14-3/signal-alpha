-- 20260629_1217_agent_results_method_signal_allow_unknown.sql
-- target: all
-- ============================================================================
-- agent_results.method_signal 에 'unknown' 허용 (C안: rules/agent 방향 abstain)
-- ----------------------------------------------------------------------------
-- 배경:
--   C안 재설계에서 소스 분석기(DART/datalab/hiring/patent rules·agent)는 방향을
--   판정하지 않고 direction="unknown" 으로 abstain 한다(판정/숫자는 메타러너 return
--   채널=src_* 모델이 산출, AGGREGATE 는 'unknown' 을 방향 합의에서 제외).
--   그러나 agent_results_method_signal_check 는 C안 이전 값(positive/negative/
--   neutral/mixed)만 허용 → ANALYZE_DART 등에서 method_signal='unknown' INSERT 가
--   "violates check constraint agent_results_method_signal_check" 로 전량 실패한다.
--   signal_events.signal_direction_check 는 이미 'unknown' 을 허용하므로 정합 차원의
--   누락. (DART 실적재+src_dart 학습 중 발견.)
-- 설계:
--   - method_signal 허용집합에 'unknown' 추가(signal_events 와 동일하게 5종).
--   - 멱등: DROP CONSTRAINT IF EXISTS 후 ADD. method_signal 은 varchar(10) 이라
--     'unknown'(7자) 수용. 기존 데이터는 부분집합이라 ADD 검증 통과.
-- ============================================================================

ALTER TABLE agent_results
    DROP CONSTRAINT IF EXISTS agent_results_method_signal_check;

ALTER TABLE agent_results
    ADD CONSTRAINT agent_results_method_signal_check
    CHECK (method_signal IN ('positive', 'negative', 'neutral', 'mixed', 'unknown'));
