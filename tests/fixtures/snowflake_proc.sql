CREATE OR REPLACE PROCEDURE analytics.clean_orders()
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
BEGIN
    DELETE FROM raw.orders WHERE quantity < 0;
    INSERT INTO analytics.orders_clean SELECT * FROM raw.orders WHERE status <> 'CANCELLED';
    RETURN 'ok';
END;
$$;