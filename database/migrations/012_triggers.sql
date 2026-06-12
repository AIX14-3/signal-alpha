-- 012_triggers.sql
-- 공통 트리거.
-- 규칙: updated_at 컬럼이 있는 모든 테이블에는 trg_<table>_updated_at 트리거를 부착한다.
-- set_final_signal_current: 같은 (stock_id, signal_date, run_key)에서 is_current는 1행만 허용.

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_stocks_updated_at
BEFORE UPDATE ON stocks
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_dart_corp_codes_updated_at
BEFORE UPDATE ON dart_corp_codes
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_dart_collection_states_updated_at
BEFORE UPDATE ON dart_collection_states
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_datalab_categories_updated_at
BEFORE UPDATE ON datalab_categories
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_datalab_category_keywords_updated_at
BEFORE UPDATE ON datalab_category_keywords
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_hiring_baseline_updated_at
BEFORE UPDATE ON hiring_baseline
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_processing_queue_updated_at
BEFORE UPDATE ON processing_queue
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_signal_subscriptions_updated_at
BEFORE UPDATE ON signal_subscriptions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_signal_journals_updated_at
BEFORE UPDATE ON signal_journals
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE FUNCTION set_final_signal_current()
RETURNS TRIGGER AS $$
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
          AND id IS DISTINCT FROM NEW.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_final_signal_current
BEFORE INSERT OR UPDATE OF is_current, stock_id, signal_date, run_key
ON final_signals
FOR EACH ROW
EXECUTE FUNCTION set_final_signal_current();
