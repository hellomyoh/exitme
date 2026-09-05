"""매매일지 ↔ 증권사 계좌 연결 + 체결 가져오기 멱등 키 (2026-09-05 지시)

검토: THROUGHLINE/docs/mjournal-broker-link-review-20260905.md
- manual_journals.broker_credential_id: 설정에 등록한 계좌 중 하나를 참조 (삭제되면 NULL)
- manual_journal_entries.broker_ref: "주문번호:일자" — 일지 안에서 유니크(부분 인덱스), 재실행 멱등
- manual_journal_entries.code: 종목코드 6자리 — 이후 가져오기는 코드로 정확 매칭

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manual_journals", sa.Column("broker_credential_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_manual_journals_broker_credential", "manual_journals", "broker_credentials",
                          ["broker_credential_id"], ["id"], ondelete="SET NULL")
    op.add_column("manual_journal_entries", sa.Column("broker_ref", sa.Text(), nullable=True))
    op.add_column("manual_journal_entries", sa.Column("code", sa.Text(), nullable=True))
    op.create_index("uq_manual_journal_entries_broker_ref", "manual_journal_entries",
                    ["journal_id", "broker_ref"], unique=True,
                    postgresql_where=sa.text("broker_ref IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_manual_journal_entries_broker_ref", table_name="manual_journal_entries")
    op.drop_column("manual_journal_entries", "code")
    op.drop_column("manual_journal_entries", "broker_ref")
    op.drop_constraint("fk_manual_journals_broker_credential", "manual_journals", type_="foreignkey")
    op.drop_column("manual_journals", "broker_credential_id")
