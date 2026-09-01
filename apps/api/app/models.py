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

from app.crypto import EncryptedBigInt


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


class OhlcvIntraday(Base):
    """분봉 하이퍼테이블 (청크 7일) — 원본 불변, 증분 수집 (feature-market-data §7)."""

    __tablename__ = "ohlcv_intraday"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)  # '1m'
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
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
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # 로그인 ID (이메일 형식 강제 아님)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 발급 계정 첫 로그인 강제


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


class TradePortfolio(TimestampMixin, Base):
    """실전 포트 — 백테스트 전환 시 파라미터 사본·backtest_id 링크 (feature-portfolio §7)."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="manual")  # manual | from_backtest
    market: Mapped[str] = mapped_column(Text, nullable=False, default="KR")     # KR | US (US 는 센트 단위)
    backtest_id: Mapped[int | None] = mapped_column(ForeignKey("backtests.id"))
    params: Mapped[dict | None] = mapped_column(JSONB)


class TradeTransaction(TimestampMixin, Base):
    """거래 원장 — buy|sell|deposit|withdraw. 금액 필드는 AES-GCM 암호화 (🔒)."""

    __tablename__ = "trade_transactions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    qty: Mapped[int | None] = mapped_column(EncryptedBigInt)          # 🔒
    price: Mapped[int | None] = mapped_column(EncryptedBigInt)        # 🔒
    amount: Mapped[int | None] = mapped_column(EncryptedBigInt)       # 🔒 (deposit/withdraw)
    realized_pnl: Mapped[int | None] = mapped_column(EncryptedBigInt) # 🔒 (sell 시 FIFO 계산)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    memo: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class PositionLot(Base):
    """FIFO 원장 로트 — 전략·백테스트와 동일 회계 (feature-portfolio §5)."""

    __tablename__ = "position_lots"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    qty_open: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)  # 🔒 잔여 수량
    price: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)     # 🔒 체결 단가
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSettings(TimestampMixin, Base):
    """사용자별 알고리즘 파라미터 오버라이드 — 기본값과의 차이만 저장 (2026-08-31 지시).

    적용 범위: 시뮬레이터 잡 생성 시점 스냅샷·포트 기준 주문표·미국 라이브 신호.
    공용 모델 신호 배치(KR)는 항상 기본값으로 계산한다.
    """

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    algo_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class PortfolioPlan(TimestampMixin, Base):
    """실전 포트의 '그날의 주문표' 스냅샷 — 일자별 매매 일지에서 계획 vs 체결 대조용 (2026-08-29 지시).

    주문표 조회 시점의 보유·현금 기준 계산 결과를 (portfolio_id, trade_date) 로 upsert 보존.
    """

    __tablename__ = "portfolio_plans"
    __table_args__ = (UniqueConstraint("portfolio_id", "trade_date"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class PositionMeta(TimestampMixin, Base):
    """포지션별 목표가·손절가 (feature-portfolio §5)."""

    __tablename__ = "position_meta"
    __table_args__ = (UniqueConstraint("portfolio_id", "instrument_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    target_price: Mapped[int | None] = mapped_column(EncryptedBigInt)  # 🔒
    stop_price: Mapped[int | None] = mapped_column(EncryptedBigInt)    # 🔒


class AssetSnapshot(Base):
    """일별 자산 스냅샷 — 추이·캘린더의 원천 (feature-dashboard §7). 배치 적재."""

    __tablename__ = "asset_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "snap_date"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    snap_date: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)   # 🔒
    stock: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)   # 🔒
    cash: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)    # 🔒
    other: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)   # 🔒


class ManualAsset(TimestampMixin, Base):
    """기타 자산 수동 등록 — 채권/펀드/금/코인/부동산 등 (feature-dashboard §5)."""

    __tablename__ = "manual_assets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[int] = mapped_column(EncryptedBigInt, nullable=False)   # 🔒


class AnalyticsEvent(Base):
    """성공 지표 이벤트 — backtest_run / portfolio_created_from_backtest / visit (ARCHITECTURE §7)."""

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
