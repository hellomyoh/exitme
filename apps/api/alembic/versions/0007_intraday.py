"""ohlcv_intraday hypertable — feature-market-data §7 (분봉, 청크 7일)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ohlcv_intraday",
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), primary_key=True),
        sa.Column("timeframe", sa.Text(), primary_key=True),  # '1m'
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open_raw", sa.BigInteger(), nullable=False),
        sa.Column("high_raw", sa.BigInteger(), nullable=False),
        sa.Column("low_raw", sa.BigInteger(), nullable=False),
        sa.Column("close_raw", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "SELECT create_hypertable('ohlcv_intraday', 'ts',"
        " chunk_time_interval => interval '7 days', migrate_data => true)"
    )
    op.create_index("ix_ohlcv_intraday_inst_ts", "ohlcv_intraday",
                    ["instrument_id", "timeframe", sa.text("ts DESC")])


def downgrade() -> None:
    op.drop_table("ohlcv_intraday")
