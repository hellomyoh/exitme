"""대시보드 API — 스냅샷·추이·캘린더·기타 자산 (feature-dashboard §5·§8)."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import AnalyticsEvent, AssetSnapshot, ManualAsset, TradePortfolio, User

router = APIRouter()

RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "ALL": 36500}


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
    today = date.today()
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
    change = snap.total - prev.total if prev else 0
    return {
        "total": snap.total, "stock": snap.stock, "cash": snap.cash, "other": snap.other,
        "change_amount": change,
        "change_pct": (change / prev.total) if prev and prev.total else None,
        "since_inception_pct": ((snap.total / first.total - 1) if first and first.total and first.snap_date < today else None),
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
    since = date.today() - timedelta(days=RANGES[key])
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
            items.append({"date": cur.snap_date.isoformat(), "pnl": cur.total - prev.total})
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
