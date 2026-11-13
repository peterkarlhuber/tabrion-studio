-- ACHTUNG: Absichtlich schreibende Anweisung (Testfall). Tabrion Studio ist nur lesend
-- und muss diese Anweisung ablehnen, bevor sie die Datenbank erreicht.
-- Preiserhöhung um 5 % für alle Produkte der Kategorie "Kochgeschirr".
UPDATE products
SET price_cents = CAST(round(price_cents * 1.05) AS INTEGER)
WHERE category_id = (SELECT category_id FROM categories WHERE slug = 'kochgeschirr');
