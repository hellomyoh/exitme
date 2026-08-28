"""users + chart layouts/drawings — ADR-003, feature-chart §7

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _ts_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        *_ts_cols(),
    )
    op.create_table(
        "chart_layouts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_table(
        "chart_drawings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("items", JSONB(), nullable=False, server_default="{}"),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", "instrument_id"),
    )


def downgrade() -> None:
    op.drop_table("chart_drawings")
    op.drop_table("chart_layouts")
    op.drop_table("users")
