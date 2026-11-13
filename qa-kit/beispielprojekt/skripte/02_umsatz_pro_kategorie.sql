-- Umsatz pro Hauptkategorie und Land (Aggregation), ohne stornierte und retournierte Bestellungen.
SELECT coalesce(parent.name, cat.name)                      AS main_category,
       c.country,
       count(DISTINCT o.order_id)                           AS orders,
       sum(i.quantity)                                      AS units,
       round(sum(i.quantity * i.unit_price_cents
                 * (1 - CAST(i.discount_pct AS REAL) / 100)) / 100.0, 2) AS revenue_eur,
       round(avg(r.rating), 2)                              AS avg_rating
FROM order_items AS i
JOIN orders AS o          ON o.order_id = i.order_id
JOIN customers AS c       ON c.customer_id = o.customer_id
JOIN products AS p        ON p.product_id = i.product_id
JOIN categories AS cat    ON cat.category_id = p.category_id
LEFT JOIN categories AS parent ON parent.category_id = cat.parent_id
LEFT JOIN reviews AS r    ON r.product_id = p.product_id AND r.customer_id = c.customer_id
WHERE o.status NOT IN ('cancelled', 'returned')
GROUP BY main_category, c.country
ORDER BY revenue_eur DESC;
