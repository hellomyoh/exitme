"""활동 로그 (2026-09-06 지시 "로깅 기능") — 실행·동기화·취소·대조 이벤트와 오류를 사용자별로 기록

거래(trade_transactions)·주문(broker_orders)은 각자 테이블이 원천이라 여기 중복 기록하지 않고,
로그 화면(GET /logs)이 세 원천을 합쳐 시간순으로 보여 준다.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("portfolio_id", sa.BigInteger(), sa.ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False, server_default="info"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_activity_logs_user_at", "activity_logs", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_activity_logs_user_at", table_name="activity_logs")
    op.drop_table("activity_logs")
