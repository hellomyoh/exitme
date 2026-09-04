"""user_settings.chat_prompt — 챗봇 추가 지침 (일반 설정, 2026-09-04 지시)

내장 시스템 프롬프트 뒤에 덧붙는 사용자별 추가 지침. 안전 규칙(내장)은 대체하지 않는다.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings",
                  sa.Column("chat_prompt", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("user_settings", "chat_prompt")
