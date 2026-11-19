-- Create a stored procedure to add a new customer
DELIMITER //
CREATE PROCEDURE AddCustomer(IN customerName VARCHAR(50))
BEGIN
    INSERT INTO customers (customer_name) VALUES (customerName);
END //
DELIMITER ;

-- Create a stored procedure to get customer orders
DELIMITER //
CREATE PROCEDURE GetCustomerOrders(IN customerId INT)
BEGIN
    SELECT orders.order_id, orders.order_date, orders.amount
    FROM orders
    WHERE orders.customer_id = customerId;
END //
DELIMITER ;
;;;
-- Create a stored procedure to update order amount
DELIMITER //
CREATE PROCEDURE UpdateOrderAmount(IN orderId INT, IN newAmount DECIMAL(10, 2))
BEGIN
    UPDATE orders
    SET amount = newAmount
    WHERE order_id = orderId;
END //
DELIMITER ;

-- Create a stored procedure to delete a customer
DELIMITER //
CREATE PROCEDURE DeleteCustomer(IN customerId INT)
BEGIN
    DELETE FROM customers WHERE customer_id = customerId;
END //
DELIMITER ;

-- Example calls to stored procedures
CALL AddCustomer('Charlie');
CALL GetCustomerOrders(1);
CALL UpdateOrderAmount(1, 175.00);
CALL DeleteCustomer(2);