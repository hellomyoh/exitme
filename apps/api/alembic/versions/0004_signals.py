"""signal snapshots + order sheets — feature-strategy-engine §7

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("regime", sa.Text()),
        sa.Column("e_target", sa.Numeric()),
        sa.Column("w_200", sa.Numeric()),
        sa.Column("w_lev", sa.Numeric()),
        sa.Column("gap_cancel_below", sa.BigInteger()),
        sa.Column("indicators", JSONB(), nullable=False, server_default="{}"),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # is_current 체인의 결정론 — 날짜당 현재 버전은 정확히 1개 (검토 로그 R4)
    op.create_index(
        "uq_signal_current_per_date", "signal_snapshots", ["trade_date"],
        unique=True, postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "order_sheets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("signal_id", sa.BigInteger(), sa.ForeignKey("signal_snapshots.id"), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("otype", sa.Text(), nullable=False),
        sa.Column("qty", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.BigInteger()),
        sa.Column("kind", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("order_sheets")
    op.drop_index("uq_signal_current_per_date", table_name="signal_snapshots")
    op.drop_table("signal_snapshots")
