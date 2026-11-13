"""products.discontinued_at and index ix_orders_status_date

Revision ID: 0003
Revises: 0002
Create Date: 2025-10-20 09:05:00
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
ALTER TABLE products ADD COLUMN discontinued_at TEXT"""
    )
    op.execute(
        """
CREATE INDEX ix_orders_status_date ON orders (status, order_date)"""
    )


def downgrade() -> None:
    op.execute(
        """
DROP INDEX ix_orders_status_date"""
    )
    op.execute(
        """
ALTER TABLE products DROP COLUMN discontinued_at"""
    )
