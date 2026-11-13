"""Payments, shipments, reviews and the reporting views

Revision ID: 0002
Revises: 0001
Create Date: 2024-06-03 14:40:00
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE payments (
    payment_id    INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders (order_id),
    method        TEXT NOT NULL CHECK (method IN ('Kreditkarte', 'PayPal', 'SEPA-Lastschrift', 'Rechnung', 'EPS', 'Sofortüberweisung')),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents <> 0),          -- negative: refund
    status        TEXT NOT NULL CHECK (status IN ('authorized', 'captured', 'refunded', 'failed')),
    paid_at       TEXT                                                 -- NULL until captured
)"""
    )
    op.execute(
        """
CREATE INDEX ix_payments_order ON payments (order_id)"""
    )
    op.execute(
        """
CREATE TABLE shipments (
    shipment_id   INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL UNIQUE REFERENCES orders (order_id),
    carrier       TEXT NOT NULL CHECK (carrier IN ('Österreichische Post', 'DHL', 'DPD', 'GLS', 'Hermes')),
    tracking_no   TEXT NOT NULL,
    shipped_at    TEXT NOT NULL,
    delivered_at  TEXT,                                                -- NULL: still on its way
    CHECK (delivered_at IS NULL OR delivered_at >= shipped_at)
)"""
    )
    op.execute(
        """
CREATE TABLE reviews (
    review_id    INTEGER PRIMARY KEY,
    product_id   INTEGER NOT NULL REFERENCES products (product_id),
    customer_id  INTEGER NOT NULL REFERENCES customers (customer_id),
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title        TEXT NOT NULL,
    body         TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (product_id, customer_id)
)"""
    )
    op.execute(
        """
CREATE VIEW monthly_revenue AS
SELECT strftime('%Y-%m', o.order_date)       AS month,
       count(*)                               AS orders,
       sum(o.total_cents)                     AS revenue_cents,
       round(sum(o.total_cents) / 100.0, 2)   AS revenue_eur,
       round(avg(o.total_cents) / 100.0, 2)   AS avg_order_eur
FROM orders AS o
WHERE o.status NOT IN ('cancelled', 'returned')
GROUP BY strftime('%Y-%m', o.order_date)"""
    )
    op.execute(
        """
CREATE VIEW top_customers AS
SELECT c.customer_id,
       c.first_name || ' ' || c.last_name     AS customer,
       c.city,
       c.country,
       count(o.order_id)                      AS orders,
       round(sum(o.total_cents) / 100.0, 2)   AS revenue_eur
FROM customers AS c
JOIN orders AS o ON o.customer_id = c.customer_id
WHERE o.status NOT IN ('cancelled', 'returned')
GROUP BY c.customer_id
ORDER BY sum(o.total_cents) DESC
LIMIT 25"""
    )


def downgrade() -> None:
    op.execute(
        """
DROP VIEW top_customers"""
    )
    op.execute(
        """
DROP VIEW monthly_revenue"""
    )
    op.execute(
        """
DROP TABLE reviews"""
    )
    op.execute(
        """
DROP TABLE shipments"""
    )
    op.execute(
        """
DROP INDEX ix_payments_order"""
    )
    op.execute(
        """
DROP TABLE payments"""
    )
