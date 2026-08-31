"""asset snapshots / manual assets / analytics events — feature-dashboard §7

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("snap_date", sa.Date(), nullable=False),
        sa.Column("total", sa.Text(), nullable=False),  # 🔒
        sa.Column("stock", sa.Text(), nullable=False),  # 🔒
        sa.Column("cash", sa.Text(), nullable=False),   # 🔒
        sa.Column("other", sa.Text(), nullable=False),  # 🔒
        sa.UniqueConstraint("user_id", "snap_date"),
    )
    op.create_table(
        "manual_assets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),  # 🔒
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_kind_time", "analytics_events", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_table("analytics_events")
    op.drop_table("manual_assets")
    op.drop_table("asset_snapshots")
