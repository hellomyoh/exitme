"""매매일지 항목별 종목 — 한 일지에 여러 종목 (2026-09-05 지시)

entries.symbol 추가 (NULL = 일지 기본 종목, 기존 행 호환). FIFO 는 종목별로 계산한다.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manual_journal_entries", sa.Column("symbol", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("manual_journal_entries", "symbol")
