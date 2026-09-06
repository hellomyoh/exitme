"""무인 실행 (2026-09-06 지시) — 승인된 지정가 주문을 09:01 시가 확인 후 정규 주문으로 발주

- user_settings.auto_exec: {"buy": bool, "sell": bool} — 설정 화면에서 매수/매도 허용을 각각 켜야 동작 (기본 모두 꺼짐)
- broker_orders.mode: 'reserve'(예약주문, 기존) | 'auto'(무인 실행 승인 줄). 상태 흐름은 broker_orders 도큐스트링 참조

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("auto_exec", postgresql.JSONB(astext_type=sa.Text()),
                                             nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("broker_orders", sa.Column("mode", sa.Text(), nullable=False, server_default="reserve"))


def downgrade() -> None:
    op.drop_column("broker_orders", "mode")
    op.drop_column("user_settings", "auto_exec")
