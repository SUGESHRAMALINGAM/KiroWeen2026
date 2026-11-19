-- Daily load for the sales mart
CREATE PROCEDURE dbo.load_daily_sales @run_date DATE, @min_qty INT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @n INT;
    DELETE FROM mart.daily_sales WHERE sale_date = @run_date;
    INSERT INTO mart.daily_sales (sale_date, region, units)
    SELECT o.order_date, c.region, SUM(o.quantity)
    FROM dbo.orders o JOIN dbo.customers c ON o.customer_id = c.customer_id
    WHERE o.order_date = @run_date
    GROUP BY o.order_date, c.region;
    SET @n = (SELECT COUNT(*) FROM mart.daily_sales);
    IF @n = 0
    BEGIN
        UPDATE dbo.load_log SET status = 'EMPTY' WHERE run_date = @run_date;
    END
END
GO
SELECT * FROM mart.daily_sales;