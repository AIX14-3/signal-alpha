-- 002_fix_final_signal_current_trigger.sql
-- final_signals 멱등 재실행 버그 수정 (set_final_signal_current 트리거).
--
-- 증상: 같은 (stock_id, signal_date, run_key, version)로 upsert를 재실행하면
--   "ON CONFLICT DO UPDATE command cannot affect row a second time"
--   (CardinalityViolation)로 실패. run_analyzers.py --loop / 일배치 당일 재실행
--   2회차부터 전부 깨짐.
--
-- 원인: INSERT ... ON CONFLICT (..., version) DO UPDATE 실행 시, BEFORE INSERT
--   트리거가 같은 (stock_id, signal_date, run_key)의 current 행을 먼저
--   is_current=FALSE로 UPDATE한다. 이 행이 곧 ON CONFLICT 대상 행이라, 한 명령
--   안에서 같은 행이 두 번 영향을 받아 Postgres가 막는다.
--
-- 수정: 강등 대상에서 "지금 upsert 중인 것과 같은 version 행"을 제외한다
--   (version IS DISTINCT FROM NEW.version). 같은 version 재upsert는 ON CONFLICT가
--   온전히 처리하고, 서로 다른 version 강등(버전 히스토리에서 단일 current 유지)은
--   그대로 동작한다. 부분 유니크 인덱스 uq_final_signal_current (WHERE is_current)
--   는 (stock_id, signal_date, run_key)당 current 1개를 계속 보장한다.

CREATE OR REPLACE FUNCTION public.set_final_signal_current()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.is_current = TRUE THEN
        PERFORM pg_advisory_xact_lock(
            hashtext(NEW.stock_id::text || '|' || NEW.signal_date::text || '|' || NEW.run_key)
        );

        UPDATE final_signals
        SET is_current = FALSE
        WHERE stock_id = NEW.stock_id
          AND signal_date = NEW.signal_date
          AND run_key = NEW.run_key
          AND is_current = TRUE
          AND id IS DISTINCT FROM NEW.id
          AND version IS DISTINCT FROM NEW.version;
    END IF;

    RETURN NEW;
END;
$function$;
