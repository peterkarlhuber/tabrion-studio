-- Bestellungen eines Kunden mit Positionen (Join mit Parametern).
-- In Studio: :customer_id als Number (z. B. 17) und :since als Text (z. B. 2025-01-01) setzen.
-- Beträge sind in Cent gespeichert; hier in Euro umgerechnet.
SELECT o.order_id,
       o.order_date,
       o.status,
       c.first_name || ' ' || c.last_name                      AS customer,
       i.line_no,
       p.name                                                   AS product,
       i.quantity,
       round(i.unit_price_cents / 100.0, 2)                     AS unit_price_eur,
       i.discount_pct,
       round(i.quantity * i.unit_price_cents
             * (1 - CAST(i.discount_pct AS REAL) / 100) / 100.0, 2) AS line_total_eur
FROM orders AS o
JOIN customers AS c   ON c.customer_id = o.customer_id
JOIN order_items AS i ON i.order_id = o.order_id
JOIN products AS p    ON p.product_id = i.product_id
WHERE o.customer_id = :customer_id
  AND o.order_date >= :since
ORDER BY o.order_date DESC, i.line_no;
