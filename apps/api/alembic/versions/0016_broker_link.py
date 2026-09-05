"""증권사 조회 연동 — 계좌 자격증명 + 체결 외부 식별자 (2026-09-05 지시)

조회 전용(주문 TR 미사용): 체결 자동 가져오기와 주문표 대조에만 쓴다.
앱키·시크릿·계좌번호는 앱 레벨 AES-GCM 암호화 컬럼으로 저장한다.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_credentials",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("portfolio_id", sa.BigInteger(),
                  sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("env", sa.Text(), nullable=False, server_default="prod"),   # prod | vps
        sa.Column("app_key", sa.Text(), nullable=False),                       # 암호문
        sa.Column("app_secret", sa.Text(), nullable=False),                    # 암호문
        sa.Column("account_no", sa.Text(), nullable=False),                    # 암호문 (종합계좌 8자리)
        sa.Column("acnt_prdt_cd", sa.Text(), nullable=False, server_default="01"),
        sa.Column("last_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # 체결 중복 방지용 외부 식별자 (주문번호+체결일자 등) — 자동 가져오기 멱등성
    op.add_column("trade_transactions", sa.Column("broker_ref", sa.Text(), nullable=True))
    op.create_index("ix_trade_transactions_broker_ref", "trade_transactions",
                    ["portfolio_id", "broker_ref"], unique=True,
                    postgresql_where=sa.text("broker_ref IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_trade_transactions_broker_ref", table_name="trade_transactions")
    op.drop_column("trade_transactions", "broker_ref")
    op.drop_table("broker_credentials")
