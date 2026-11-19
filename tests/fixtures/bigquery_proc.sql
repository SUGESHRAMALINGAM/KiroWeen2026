CREATE OR REPLACE PROCEDURE `proj.ds.rebuild`(day DATE)
BEGIN
  DECLARE n INT64;
  CREATE OR REPLACE TABLE `proj.ds.daily` AS
  SELECT order_date, COUNT(*) AS orders FROM `proj.ds.orders` WHERE order_date = day GROUP BY order_date;
  UPDATE `proj.ds.status` SET done = TRUE WHERE job = 'daily';
END;