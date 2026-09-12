"""실전 로트의 전략 메타데이터 (감사 A1·A2, 2026-09-12) — 체결 시점의 로트 종류·익절가를 원장에, 계획일 레짐·Grid 를 주문에.

- trade_transactions.lot_kind: 매수 = grid|core|lev_strat|lev_tact1|lev_tact2, 매도 = tp|reduce|lev_strat|lev_tact_exit|lev_liq. NULL = 태그 없음(근사).
- trade_transactions.tp_price(🔒): 매수 grid 로트의 익절가 스냅샷 / 매도 tp 의 지정가(귀속 로트 식별).
- broker_orders.plan_grid·plan_regime: 발주 시점의 계획 Grid·레짐 — 체결 가져오기가 주문번호로 태그를 만든다.
기존 행은 전부 NULL 로 남고 종전 근사가 그대로 적용된다. 되돌림은 열 제거.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trade_transactions", sa.Column("lot_kind", sa.Text(), nullable=True))
    op.add_column("trade_transactions", sa.Column("tp_price", sa.Text(), nullable=True))  # 🔒 EncryptedBigInt
    op.add_column("broker_orders", sa.Column("plan_grid", sa.Numeric(), nullable=True))
    op.add_column("broker_orders", sa.Column("plan_regime", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("broker_orders", "plan_regime")
    op.drop_column("broker_orders", "plan_grid")
    op.drop_column("trade_transactions", "tp_price")
    op.drop_column("trade_transactions", "lot_kind")
