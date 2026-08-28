"""portfolio daily plan snapshots — 실전 일지의 '그날의 주문표' 보존 (2026-08-29 지시)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_plans",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("portfolio_id", sa.BigInteger(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("portfolio_id", "trade_date"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_plans")
