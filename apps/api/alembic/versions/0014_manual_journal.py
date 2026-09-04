"""수동 주식 매매일지 — 일지(이름·종목·증권사·요율) + 항목(구분·수량·단가) (2026-09-05 지시)

전략 포트와 무관한 자유 기록용. 실현손익·수익률·보유기간·비용은 FIFO 로 서버가 계산한다.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_journals",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),          # 종목명 (자유 텍스트)
        sa.Column("broker", sa.Text(), nullable=False, server_default=""),
        sa.Column("fee_rate", sa.Numeric(), nullable=False, server_default="0.00015"),   # 수수료율 (비율)
        sa.Column("tax_rate", sa.Numeric(), nullable=False, server_default="0.0023"),    # 제세금율 (매도, 비율)
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_manual_journals_user", "manual_journals", ["user_id"])
    op.create_table(
        "manual_journal_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("journal_id", sa.BigInteger(),
                  sa.ForeignKey("manual_journals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),            # buy | sell
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("qty", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),     # 단가 (원)
        sa.Column("reason", sa.Text(), nullable=True),           # 매매 이유 (선택)
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_manual_journal_entries_journal", "manual_journal_entries", ["journal_id"])


def downgrade() -> None:
    op.drop_table("manual_journal_entries")
    op.drop_table("manual_journals")
