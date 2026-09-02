"""portfolio snapshots — 포트 단위 일별 자산 스냅샷 (ADR-008, 2026-09-02)

사용자 asset_snapshots 는 이 테이블의 KRW 합산 + 기타 자산으로 유도된다.
FK 는 CASCADE — delete_portfolio 의 수동 자식 삭제 목록에 의존하지 않는다 (0008 FK 위반 사고 재발 방지).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("portfolio_id", sa.BigInteger(),
                  sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snap_date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Text(), nullable=False),       # 🔒 정수 확정 후 암호화
        sa.Column("stock_value", sa.Text(), nullable=False),  # 🔒
        sa.Column("cash", sa.Text(), nullable=False),         # 🔒
        sa.Column("currency", sa.Text(), nullable=False),     # KRW | USD(센트) — 적재 시점 비정규화
        sa.UniqueConstraint("portfolio_id", "snap_date", name="uq_portfolio_snapshots_pid_date"),
    )
    op.create_index("ix_portfolio_snapshots_pid_date", "portfolio_snapshots",
                    ["portfolio_id", "snap_date"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_snapshots_pid_date", table_name="portfolio_snapshots")
    op.drop_table("portfolio_snapshots")
