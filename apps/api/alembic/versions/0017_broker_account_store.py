"""증권사 계좌를 설정에서 관리 — 계정 단위 저장 + 포트 연결 (2026-09-05 지시)

기존: 자격이 포트에 1:1 종속(포트마다 키 재입력).
변경: broker_credentials 는 **사용자 계좌 목록**이 되고, 포트는 그중 하나를 참조한다.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_credentials",
                  sa.Column("label", sa.Text(), nullable=False, server_default=""))
    op.add_column("portfolios", sa.Column("broker_credential_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_portfolios_broker_credential", "portfolios", "broker_credentials",
                          ["broker_credential_id"], ["id"], ondelete="SET NULL")
    # 기존 1:1 연결 이관 후 종속 컬럼 제거
    op.execute("UPDATE portfolios p SET broker_credential_id = bc.id "
               "FROM broker_credentials bc WHERE bc.portfolio_id = p.id")
    op.drop_column("broker_credentials", "portfolio_id")


def downgrade() -> None:
    op.add_column("broker_credentials", sa.Column("portfolio_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE broker_credentials bc SET portfolio_id = p.id "
               "FROM portfolios p WHERE p.broker_credential_id = bc.id")
    op.drop_constraint("fk_portfolios_broker_credential", "portfolios", type_="foreignkey")
    op.drop_column("portfolios", "broker_credential_id")
    op.drop_column("broker_credentials", "label")
