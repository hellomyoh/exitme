"""backtests + backtest_equity — feature-backtest §7

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtests",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("progress", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("data_fingerprint", sa.Text()),
        sa.Column("kpi", JSONB()),
        sa.Column("trades", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_backtests_user", "backtests", ["user_id", sa.text("id DESC")])
    op.create_table(
        "backtest_equity",
        sa.Column("backtest_id", sa.BigInteger(), sa.ForeignKey("backtests.id"), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("equity", sa.Numeric(), nullable=False),
        sa.Column("benchmark", sa.Numeric(), nullable=False),
        sa.Column("regime", sa.Text(), nullable=False),
        sa.Column("exposure", sa.Numeric(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_equity")
    op.drop_table("backtests")
