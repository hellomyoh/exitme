"""portfolios / transactions / lots / meta — feature-portfolio §7

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("backtest_id", sa.BigInteger(), sa.ForeignKey("backtests.id")),
        sa.Column("params", JSONB()),
        *_ts(),
    )
    op.create_table(
        "trade_transactions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("portfolio_id", sa.BigInteger(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id")),
        sa.Column("qty", sa.Text()),           # 🔒 EncryptedBigInt
        sa.Column("price", sa.Text()),         # 🔒
        sa.Column("amount", sa.Text()),        # 🔒
        sa.Column("realized_pnl", sa.Text()),  # 🔒
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("memo", sa.Text()),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        *_ts(),
    )
    op.create_index("ix_trade_tx_portfolio", "trade_transactions", ["portfolio_id", "executed_at"])
    op.create_table(
        "position_lots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("portfolio_id", sa.BigInteger(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("qty_open", sa.Text(), nullable=False),  # 🔒
        sa.Column("price", sa.Text(), nullable=False),     # 🔒
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lots_portfolio", "position_lots", ["portfolio_id", "instrument_id", "id"])
    op.create_table(
        "position_meta",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("portfolio_id", sa.BigInteger(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("target_price", sa.Text()),  # 🔒
        sa.Column("stop_price", sa.Text()),    # 🔒
        *_ts(),
        sa.UniqueConstraint("portfolio_id", "instrument_id"),
    )


def downgrade() -> None:
    op.drop_table("position_meta")
    op.drop_table("position_lots")
    op.drop_table("trade_transactions")
    op.drop_table("portfolios")
