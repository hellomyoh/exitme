"""매매일지 청산 + 스냅샷에 매매일지 자산 (2026-09-05 지시)

- manual_journals.closed_at: 청산 처리 시각 (NULL = 진행 중). 청산 일지는 대시보드·총자산에서 제외
- asset_snapshots.journal: 매매일지 종합 자산(취득원가 합, 암호화 텍스트). NULL = 0 (구 스냅샷 호환)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manual_journals", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("asset_snapshots", sa.Column("journal", sa.Text(), nullable=True))  # 🔒


def downgrade() -> None:
    op.drop_column("asset_snapshots", "journal")
    op.drop_column("manual_journals", "closed_at")
