CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stocks_updated_at ON stocks;
CREATE TRIGGER trg_stocks_updated_at
BEFORE UPDATE ON stocks
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_processing_queue_updated_at ON processing_queue;
CREATE TRIGGER trg_processing_queue_updated_at
BEFORE UPDATE ON processing_queue
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_signal_subscriptions_updated_at ON signal_subscriptions;
CREATE TRIGGER trg_signal_subscriptions_updated_at
BEFORE UPDATE ON signal_subscriptions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_signal_journals_updated_at ON signal_journals;
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

DROP TRIGGER IF EXISTS trg_final_signal_current ON final_signals;

CREATE TRIGGER trg_final_signal_current
BEFORE INSERT OR UPDATE OF is_current, stock_id, signal_date, run_key
ON final_signals
FOR EACH ROW
EXECUTE FUNCTION set_final_signal_current();
