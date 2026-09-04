"""app_settings — 전역 앱 설정 키·값 (챗봇 시스템 프롬프트, 2026-09-04 지시)

관리자만 쓰는 전역 설정 저장소. 첫 사용처: chat_system_prompt (비어 있으면 내장 기본 사용).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
