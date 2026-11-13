"""Initial shop schema: categories, customers, products, orders, order_items

Revision ID: 0001
Revises: 
Create Date: 2024-01-08 10:15:00
"""
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES categories (category_id) ON DELETE RESTRICT,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE CHECK (slug = lower(slug) AND slug NOT LIKE '% %')
)"""
    )
    op.execute(
        """
CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    email        TEXT NOT NULL UNIQUE CHECK (email LIKE '%_@_%._%'),
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    phone        TEXT,                      -- NULL: no phone number given
    street       TEXT NOT NULL,
    postal_code  TEXT NOT NULL,
    city         TEXT NOT NULL,
    country      TEXT NOT NULL CHECK (country IN ('AT', 'DE')),
    birth_date   TEXT CHECK (birth_date IS NULL OR birth_date = date(birth_date)),  -- ISO date, optional
    newsletter   INTEGER NOT NULL DEFAULT 0 CHECK (newsletter IN (0, 1)),
    created_at   TEXT NOT NULL CHECK (created_at = datetime(created_at))
)"""
    )
    op.execute(
        """
CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    sku            TEXT NOT NULL UNIQUE,
    category_id    INTEGER NOT NULL REFERENCES categories (category_id),
    name           TEXT NOT NULL,
    description    TEXT,
    price_cents    INTEGER NOT NULL CHECK (price_cents > 0),            -- gross price in euro cents
    vat_rate_pct   TEXT NOT NULL DEFAULT '20.00' CHECK (vat_rate_pct IN ('10.00', '13.00', '20.00')),  -- decimal text
    weight_grams   INTEGER CHECK (weight_grams IS NULL OR weight_grams > 0),
    stock          INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    thumbnail      BLOB,                                               -- small PNG placeholder, NULL for some
    created_at     TEXT NOT NULL
)"""
    )
    op.execute(
        """
CREATE INDEX ix_products_category ON products (category_id)"""
    )
    op.execute(
        """
CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date      TEXT NOT NULL CHECK (order_date = datetime(order_date)),
    status          TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'returned')),
    currency        TEXT NOT NULL DEFAULT 'EUR' CHECK (currency = 'EUR'),
    shipping_cents  INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
    total_cents     INTEGER NOT NULL CHECK (total_cents >= 0),          -- items after discount + shipping
    voucher_code    TEXT,                                               -- NULL: no voucher
    note            TEXT                                                -- customer's note, mostly NULL
)"""
    )
    op.execute(
        """
CREATE INDEX ix_orders_customer ON orders (customer_id)"""
    )
    op.execute(
        """
CREATE INDEX ix_orders_date ON orders (order_date)"""
    )
    op.execute(
        """
CREATE TABLE order_items (
    order_id          INTEGER NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    line_no           INTEGER NOT NULL CHECK (line_no >= 1),
    product_id        INTEGER NOT NULL REFERENCES products (product_id),
    quantity          INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 99),
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents > 0),
    discount_pct      TEXT NOT NULL DEFAULT '0.00'
                      CHECK (CAST(discount_pct AS REAL) >= 0 AND CAST(discount_pct AS REAL) <= 50),  -- decimal text
    PRIMARY KEY (order_id, line_no)
)"""
    )
    op.execute(
        """
CREATE INDEX ix_order_items_product ON order_items (product_id)"""
    )


def downgrade() -> None:
    op.execute(
        """
DROP INDEX ix_order_items_product"""
    )
    op.execute(
        """
DROP TABLE order_items"""
    )
    op.execute(
        """
DROP INDEX ix_orders_date"""
    )
    op.execute(
        """
DROP INDEX ix_orders_customer"""
    )
    op.execute(
        """
DROP TABLE orders"""
    )
    op.execute(
        """
DROP INDEX ix_products_category"""
    )
    op.execute(
        """
DROP TABLE products"""
    )
    op.execute(
        """
DROP TABLE customers"""
    )
    op.execute(
        """
DROP TABLE categories"""
    )
