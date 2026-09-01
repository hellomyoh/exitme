"""admin accounts — is_admin / must_change_password (2026-09-01 지시)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "is_admin")
