"""장 시작 전 예상체결가 표본 적재 (2026-09-15 지시) — 정확도를 나중에 잴 수 있게 남긴다.

`preopen_watch` 는 08:30~09:10 매 분 예상체결가(09:00 이후는 확정 시가)를 관찰하지만 Redis 에 **TTL 12시간**으로만
두어 다음 날이면 사라진다. KIS 는 과거 예상체결가를 주지 않고 분봉도 09:00 부터라, "예상가가 실제 시가와 얼마나
맞는가"를 잴 자료가 어디에도 없다 — docs/preopen-order-timing-review-20260915.md §4.

이 표는 그 표본을 남기기만 한다. **판정에 쓰지 않는다** (발주는 09:01 확정 시가 그대로). 급락일이 연 4~5회라
의미 있는 표본이 쌓이려면 시간이 필요하므로 지금 시작한다.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preopen_samples",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),   # 관찰 시각 (KST)
        sa.Column("price", sa.BigInteger(), nullable=False),
        # expected = 동시호가 예상체결가 · open = 09:00 확정 시가 · current = 시가 미확정 시 현재가
        sa.Column("kind", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("code", "trade_date", "at", name="pk_preopen_samples"),
    )
    op.create_index("ix_preopen_samples_date", "preopen_samples", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_preopen_samples_date", table_name="preopen_samples")
    op.drop_table("preopen_samples")
