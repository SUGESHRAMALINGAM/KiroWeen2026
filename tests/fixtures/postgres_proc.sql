CREATE OR REPLACE FUNCTION refresh_totals(p_day date) RETURNS void AS $$
DECLARE
    v_rows int;
BEGIN
    DELETE FROM totals WHERE day = p_day;
    INSERT INTO totals (day, customer_id, units)
    SELECT order_date, customer_id, SUM(quantity) FROM orders WHERE order_date = p_day GROUP BY 1, 2;
    IF v_rows > 0 THEN
        UPDATE audit SET last_run = now() WHERE job = 'totals';
    END IF;
    RAISE NOTICE 'done';
END;
$$ LANGUAGE plpgsql;