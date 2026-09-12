"""매매일지 일별 스냅샷 (2026-09-12 지시) — 자산 추이에서 일지를 하나씩 선으로 그리기 위한 원천.

포트는 portfolio_snapshots 로 일별 값을 남기는데 매매일지는 사용자 합계 한 칸(asset_snapshots.journal)만 있어
일지별 과거 값이 없었다. 같은 구조의 테이블을 두고 매일 스냅샷과 함께 적재한다.
approx = 소급 재계산분(기록·종가로 되살린 값) 표시 — 당시의 실전매매 중복 제외 상태까지는 복원하지 못한다.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("journal_id", sa.BigInteger(), nullable=False),
        sa.Column("snap_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),    # 🔒 EncryptedBigInt — 총자산에 넣는 평가액
        sa.Column("cost", sa.Text(), nullable=False),     # 🔒 EncryptedBigInt — 보유 취득원가
        sa.Column("counted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approx", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["journal_id"], ["manual_journals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("journal_id", "snap_date", name="uq_journal_snapshots_jid_date"),
    )
    op.create_index("ix_journal_snapshots_date", "journal_snapshots", ["snap_date"])


def downgrade() -> None:
    op.drop_index("ix_journal_snapshots_date", table_name="journal_snapshots")
    op.drop_table("journal_snapshots")
