"""무인 실행 허용을 증권사 계좌별로 (2026-09-07 지시) — broker_credentials.auto_exec

- {"buy": bool, "sell": bool, "preopen_cancel": bool}. 계좌가 진실의 원천이고, user_settings.auto_exec 는 새 계좌에 적용되는 기본값으로 남는다.
- 데이터 이전: 기존 사용자 설정값을 그 사용자의 모든 계좌에 복사해 동작이 바뀌지 않게 한다.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_credentials", sa.Column("auto_exec", postgresql.JSONB(astext_type=sa.Text()),
                                                  nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.execute("UPDATE broker_credentials bc SET auto_exec = COALESCE(us.auto_exec, '{}'::jsonb) "
               "FROM user_settings us WHERE us.user_id = bc.user_id")


def downgrade() -> None:
    op.drop_column("broker_credentials", "auto_exec")
