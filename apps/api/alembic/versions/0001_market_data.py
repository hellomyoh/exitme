"""market data schema — feature-market-data.md §7, ADR-002

Revision ID: 0001
Revises:
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "instruments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "symbol_status_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("action_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ratio", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "trading_calendar",
        sa.Column("cal_date", sa.Date(), primary_key=True),
        sa.Column("is_open", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "batch_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "seed_checkpoints",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("year", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", "year"),
    )

    op.create_table(
        "ohlcv_daily",
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id"), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("open_raw", sa.BigInteger(), nullable=False),
        sa.Column("high_raw", sa.BigInteger(), nullable=False),
        sa.Column("low_raw", sa.BigInteger(), nullable=False),
        sa.Column("close_raw", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("adj_factor", sa.Numeric(), nullable=False, server_default="1"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # 일봉 하이퍼테이블 — 청크 1년 (ADR-002)
    op.execute(
        "SELECT create_hypertable('ohlcv_daily', 'trade_date',"
        " chunk_time_interval => interval '1 year', migrate_data => true)"
    )
    op.create_index("ix_ohlcv_daily_inst_date", "ohlcv_daily", ["instrument_id", sa.text("trade_date DESC")])


def downgrade() -> None:
    op.drop_table("ohlcv_daily")
    op.drop_table("seed_checkpoints")
    op.drop_table("batch_runs")
    op.drop_table("trading_calendar")
    op.drop_table("corporate_actions")
    op.drop_table("symbol_status_history")
    op.drop_table("instruments")
