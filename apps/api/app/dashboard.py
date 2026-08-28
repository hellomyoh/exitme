"""대시보드 API — 스냅샷·추이·캘린더·기타 자산 (feature-dashboard §5·§8)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import AnalyticsEvent, AssetSnapshot, ManualAsset, TradePortfolio, User

router = APIRouter()

RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "ALL": 36500}

KST = timezone(timedelta(hours=9))


def kst_today() -> date:
    """한국 거래일 기준 오늘 — UTC date.today() 는 00~09시(KST) 사이 하루 밀림 (검증 M-7)."""
    return datetime.now(KST).date()


def user_flows_between(session: Session, user_id: int, d_from: date, d_to: date) -> int:
    """(d_from, d_to] 구간 사용자 전체 외부 현금흐름 합 — 입금 +, 출금 − (검증 C-3·M-6).

    입출금은 자산 증감이 아니라 이동이므로, 손익 표기는 반드시 흐름을 차감해야 한다.
    """
    from app.models import TradeTransaction

    pids = select(TradePortfolio.id).where(TradePortfolio.user_id == user_id)
    txs = session.scalars(select(TradeTransaction).where(
        TradeTransaction.portfolio_id.in_(pids),
        TradeTransaction.kind.in_(("deposit", "withdraw")))).all()
    total = 0
    for t in txs:
        d = t.executed_at.astimezone(KST).date() if t.executed_at.tzinfo else t.executed_at.date()
        if d_from < d <= d_to:
            total += t.amount if t.kind == "deposit" else -t.amount
    return total


def record_event(session: Session, kind: str, user_id: int | None) -> None:
    session.add(AnalyticsEvent(user_id=user_id, kind=kind))


def compute_user_snapshot(session: Session, user_id: int, snap_date: date) -> AssetSnapshot:
    """사용자 자산 스냅샷 계산·저장 (같은 날짜는 갱신) — 배치와 API 가 공유."""
    from app.portfolios import latest_close
    from app.models import PositionLot, TradeTransaction

    pfs = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)).all()
    stock = 0.0
    cash = 0
    for pf in pfs:
        lots = session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pf.id)).all()
        for l in lots:
            px = latest_close(session, l.instrument_id)
            stock += l.qty_open * (px[0] if px else l.price)
        txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pf.id)).all()
        for t in txs:
            if t.kind == "deposit":
                cash += t.amount
            elif t.kind == "withdraw":
                cash -= t.amount
            elif t.kind == "buy":
                cash -= t.qty * t.price
            elif t.kind == "sell":
                cash += t.qty * t.price
    other = sum(m.value for m in session.scalars(
        select(ManualAsset).where(ManualAsset.user_id == user_id)).all())
    snap = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == snap_date))
    if snap is None:
        snap = AssetSnapshot(user_id=user_id, snap_date=snap_date, total=0, stock=0, cash=0, other=0)
        session.add(snap)
    snap.stock, snap.cash, snap.other = round(stock), cash, other
    snap.total = round(stock) + cash + other
    session.flush()
    return snap


@router.get("/dashboard")
def dashboard(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    record_event(session, "visit", user_id)
    today = kst_today()
    snap = compute_user_snapshot(session, user_id, today)  # 열람 시점 최신화
    prev = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date < today)
        .order_by(AssetSnapshot.snap_date.desc()).limit(1)
    ).first()
    first = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id)
        .order_by(AssetSnapshot.snap_date).limit(1)
    ).first()
    session.commit()
    manuals = session.scalars(select(ManualAsset).where(ManualAsset.user_id == user_id)).all()
    # 전일 대비·누적 손익은 외부 입출금을 차감한 순수 성과 (단순 Dietz, 검증 C-3·M-6)
    change = 0
    change_pct = None
    if prev:
        f = user_flows_between(session, user_id, prev.snap_date, today)
        change = snap.total - prev.total - f
        denom = prev.total + f
        change_pct = change / denom if denom > 0 else None
    since_pct = None
    if first and first.snap_date < today:
        f_all = user_flows_between(session, user_id, first.snap_date, today)
        denom = first.total + f_all
        since_pct = (snap.total - first.total - f_all) / denom if denom > 0 else None
    return {
        "total": snap.total, "stock": snap.stock, "cash": snap.cash, "other": snap.other,
        "change_amount": change,
        "change_pct": change_pct,
        "since_inception_pct": since_pct,
        "manual_assets": [
            {"id": m.id, "name": m.name, "category": m.category, "value": m.value} for m in manuals
        ],
    }


@router.get("/portfolio/trend")
def trend(range_: str = "3M", user_id: int = Depends(current_user_id),
          session: Session = Depends(get_session)) -> dict:
    key = range_.upper()
    if key not in RANGES:
        raise HTTPException(status_code=422, detail=f"range must be one of {list(RANGES)}")
    since = kst_today() - timedelta(days=RANGES[key])
    rows = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date >= since)
        .order_by(AssetSnapshot.snap_date)
    ).all()
    return {"items": [
        {"date": r.snap_date.isoformat(), "total": r.total, "stock": r.stock,
         "cash": r.cash, "other": r.other} for r in rows
    ]}


@router.get("/portfolio/calendar")
def calendar(month: str, user_id: int = Depends(current_user_id),
             session: Session = Depends(get_session)) -> dict:
    """일간 손익 캘린더 — 스냅샷 전일 대비 증감."""
    try:
        first = date.fromisoformat(month + "-01")
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id,
                                    AssetSnapshot.snap_date >= first - timedelta(days=7),
                                    AssetSnapshot.snap_date < nxt)
        .order_by(AssetSnapshot.snap_date)
    ).all()
    items = []
    for prev, cur in zip(rows, rows[1:]):
        if cur.snap_date >= first:
            f = user_flows_between(session, user_id, prev.snap_date, cur.snap_date)
            items.append({"date": cur.snap_date.isoformat(), "pnl": cur.total - prev.total - f})
    return {"items": items}


class ManualAssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=40)
    value: int = Field(ge=0)


@router.post("/manual-assets", status_code=201)
def create_manual(body: ManualAssetIn, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    m = ManualAsset(user_id=user_id, name=body.name, category=body.category, value=body.value)
    session.add(m)
    session.commit()
    return {"id": m.id}


@router.patch("/manual-assets/{mid}")
def update_manual(mid: int, body: ManualAssetIn, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    m = session.get(ManualAsset, mid)
    if m is None or m.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    m.name, m.category, m.value = body.name, body.category, body.value
    session.commit()
    return {"id": m.id}


@router.delete("/manual-assets/{mid}")
def delete_manual(mid: int, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    m = session.get(ManualAsset, mid)
    if m is None or m.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    session.delete(m)
    session.commit()
    return {"deleted": mid}
