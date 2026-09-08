"""ADR-009 전환 정리 (2026-09-08) — 승인 단계 폐지로 09:01 실행기가 더 읽지 않는 broker_orders.status='approved' 행을 cancelled 로.

- 데이터 이전만(스키마 변경 없음). 종전 16:45 자동 승인이 남긴 행이 화면에 '무인 승인(구)'로 떠 있지 않게 한다.
- 되돌림은 없음 — 승인 단계 자체가 사라졌으므로 복원할 의미가 없다.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-08
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE broker_orders SET status = 'cancelled', "
               "message = 'ADR-009 전환 정리 — 승인 단계 폐지(2026-09-08), 09:01 단일 실행이 대신 발주' "
               "WHERE status = 'approved'")


def downgrade() -> None:
    pass
