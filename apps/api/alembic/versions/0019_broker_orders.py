"""증권사 예약주문 접수 기록 (2026-09-05 지시: 주문표 버튼 → 예약주문)

- broker_orders: 주문표 한 줄당 한 행. KIS 예약주문순번·실제 주문번호·체결수량·상태를 기록.
- 활성(reserved) 상태에서 같은 주문표 줄의 중복 접수를 막는 부분 유니크 인덱스.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("portfolio_id", sa.BigInteger(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker_credential_id", sa.BigInteger(),
                  sa.ForeignKey("broker_credentials.id", ondelete="SET NULL"), nullable=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("line_key", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("otype", sa.Text(), nullable=False),
        sa.Column("qty", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("rsvn_ord_seq", sa.Text(), nullable=True),
        sa.Column("order_no", sa.Text(), nullable=True),
        sa.Column("filled_qty", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="reserved"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_broker_orders_portfolio_date", "broker_orders", ["portfolio_id", "plan_date"])
    op.create_index("uq_broker_orders_active_line", "broker_orders", ["portfolio_id", "plan_date", "line_key"],
                    unique=True, postgresql_where=sa.text("status = 'reserved'"))


def downgrade() -> None:
    op.drop_index("uq_broker_orders_active_line", table_name="broker_orders")
    op.drop_index("ix_broker_orders_portfolio_date", table_name="broker_orders")
    op.drop_table("broker_orders")
