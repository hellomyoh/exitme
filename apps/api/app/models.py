"""시세 도메인 모델 — 스키마 명세는 THROUGHLINE/features/feature-market-data.md §7.

원칙(ARCHITECTURE §3): 가격은 정수(원), 원본(_raw) 불변 + adj_factor 별도,
거래정지·상폐는 symbol_status_history 시점 속성으로 관리.
"""
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # 예: "069500"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)  # KOSPI | KOSDAQ
    type: Mapped[str] = mapped_column(Text, nullable=False, default="ETF")  # ETF | STOCK


class SymbolStatusHistory(TimestampMixin, Base):
    """거래정지·상폐의 시점 속성 — 생존 편향 방지 (ADR-002)."""

    __tablename__ = "symbol_status_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)  # NULL = 현재 유효
    status: Mapped[str] = mapped_column(Text, nullable=False)  # listed | halted | delisted


class CorporateAction(TimestampMixin, Base):
    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    action_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # split | merge | dividend ...
    ratio: Mapped[float] = mapped_column(Numeric, nullable=False)


class TradingCalendar(Base):
    """거래일 캘린더 — 전략·백테스트의 단일 소스 (pykrx 시딩)."""

    __tablename__ = "trading_calendar"

    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)


class BatchRun(TimestampMixin, Base):
    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # seed | daily_ingest | signal ...
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")  # running | ok | failed
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class OhlcvDaily(Base):
    """일봉 하이퍼테이블 (청크 1년). 원본 가격 불변 — UPDATE 금지, 수정주가는 adj_factor 곱으로 산출."""

    __tablename__ = "ohlcv_daily"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adj_factor: Mapped[float] = mapped_column(Numeric, nullable=False, default=1)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # kis | pykrx
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SeedCheckpoint(Base):
    """시딩 이어받기 체크포인트 — (종목, 연도) 단위 완료 기록."""

    __tablename__ = "seed_checkpoints"
    __table_args__ = (UniqueConstraint("code", "year"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)


class ChartLayout(TimestampMixin, Base):
    """차트 레이아웃 — 지표 구성·주기 등 JSON (feature-chart §7). 소유자 격리."""

    __tablename__ = "chart_layouts"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ChartDrawing(TimestampMixin, Base):
    """종목별 드로잉 JSON (feature-chart §7). 소유자 격리."""

    __tablename__ = "chart_drawings"
    __table_args__ = (UniqueConstraint("user_id", "instrument_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    items: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Backtest(TimestampMixin, Base):
    """백테스트 잡 — 결과는 단일 트랜잭션 저장, data_fingerprint 로 stale 판정 (feature-backtest §7·§8)."""

    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="QUEUED")  # QUEUED|RUNNING|DONE|FAILED|CANCELED
    progress: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    data_fingerprint: Mapped[str | None] = mapped_column(Text)
    kpi: Mapped[dict | None] = mapped_column(JSONB)
    trades: Mapped[list | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class BacktestEquity(Base):
    __tablename__ = "backtest_equity"

    backtest_id: Mapped[int] = mapped_column(ForeignKey("backtests.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    equity: Mapped[float] = mapped_column(Numeric, nullable=False)
    benchmark: Mapped[float] = mapped_column(Numeric, nullable=False)
    regime: Mapped[str] = mapped_column(Text, nullable=False)
    exposure: Mapped[float] = mapped_column(Numeric, nullable=False)


class SignalSnapshot(TimestampMixin, Base):
    """일일 시그널 — append-only, is_current 체인 (feature-strategy-engine §7)."""

    __tablename__ = "signal_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)   # 신호 기준일 (종가 확정일)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)        # OK | INSUFFICIENT_HISTORY | MISSING | FAILED
    regime: Mapped[str | None] = mapped_column(Text)
    e_target: Mapped[float | None] = mapped_column(Numeric)
    w_200: Mapped[float | None] = mapped_column(Numeric)
    w_lev: Mapped[float | None] = mapped_column(Numeric)
    gap_cancel_below: Mapped[int | None] = mapped_column(BigInteger)
    indicators: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class OrderSheetRow(Base):
    """주문표 행 — append-only (수정 금지)."""

    __tablename__ = "order_sheets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signal_snapshots.id"), nullable=False)
    instrument: Mapped[str] = mapped_column(Text, nullable=False)   # K200 | LEV
    side: Mapped[str] = mapped_column(Text, nullable=False)
    otype: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
