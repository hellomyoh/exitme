"""매매일지 직접 주문 (ADR-011 PR 2, 2026-09-10 지시) — broker_orders 를 실전 포트와 매매일지가 함께 쓴다.

- `journal_id` 추가(매매일지 주문), `portfolio_id` 를 nullable 로 완화. 둘 중 하나는 반드시 있어야 한다(CHECK).
- 기존 행은 전부 portfolio_id 가 있으므로 데이터 이전 없음. 되돌림은 journal_id 행을 지운 뒤 NOT NULL 복구.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_orders", sa.Column("journal_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_broker_orders_journal", "broker_orders", "manual_journals",
                          ["journal_id"], ["id"], ondelete="CASCADE")
    op.alter_column("broker_orders", "portfolio_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_check_constraint("ck_broker_orders_owner", "broker_orders",
                               "portfolio_id IS NOT NULL OR journal_id IS NOT NULL")
    op.create_index("ix_broker_orders_journal_date", "broker_orders", ["journal_id", "plan_date"])


def downgrade() -> None:
    op.execute("DELETE FROM broker_orders WHERE journal_id IS NOT NULL")
    op.drop_index("ix_broker_orders_journal_date", table_name="broker_orders")
    op.drop_constraint("ck_broker_orders_owner", "broker_orders", type_="check")
    op.alter_column("broker_orders", "portfolio_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_constraint("fk_broker_orders_journal", "broker_orders", type_="foreignkey")
    op.drop_column("broker_orders", "journal_id")
