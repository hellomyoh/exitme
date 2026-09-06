"""텔레그램 알림 (2026-09-07 지시) — 사용자 설정에 봇 토큰(앱 암호화)·채팅 ID·보낼 항목

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("telegram_bot_token", sa.Text(), nullable=True))   # 🔒 EncryptedText (앱 계층)
    op.add_column("user_settings", sa.Column("telegram_chat_id", sa.Text(), nullable=True))
    op.add_column("user_settings", sa.Column("notify", postgresql.JSONB(astext_type=sa.Text()),
                                             nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("user_settings", "notify")
    op.drop_column("user_settings", "telegram_chat_id")
    op.drop_column("user_settings", "telegram_bot_token")
