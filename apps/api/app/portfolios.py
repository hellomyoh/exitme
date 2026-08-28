"""실전매매 기록 API — FIFO 원장·XIRR/TWR·전환 (feature-portfolio §5·§8)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import (
    Backtest,
    Instrument,
    OhlcvDaily,
    PositionLot,
    PositionMeta,
    TradePortfolio,
    TradeTransaction,
)

router = APIRouter()

ANNUALIZE_MIN_DAYS = 30  # 보유 30일 미만 연환산 미표시 (feature-portfolio §5)


# ── 수익률 수학
def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """이분법 XIRR — cashflows: (일자, 금액) 입금 −, 회수/평가 +. 해 없으면 None."""
    if len(cashflows) < 2:
        return None
    t0 = cashflows[0][0]

    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** ((d - t0).days / 365.0) for d, cf in cashflows)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def twr(daily: list[tuple[date, float, float]]) -> float | None:
    """TWR 일별 체인 — daily: (일자, 그날 종가 평가액, 그날 외부 현금흐름 합(입금+)).

    수익률_t = (V_t − F_t) / V_{t−1}. V_{t−1} = 0 인 구간은 건너뛴다.
    """
    if len(daily) < 2:
        return None
    acc = 1.0
    for (_, v_prev, _), (_, v, f) in zip(daily, daily[1:]):
        if v_prev > 0:
            acc *= (v - f) / v_prev
    return acc - 1.0


def latest_close(session: Session, instrument_id: int) -> tuple[float, date] | None:
    row = session.execute(
        select(OhlcvDaily).where(OhlcvDaily.instrument_id == instrument_id)
        .order_by(OhlcvDaily.trade_date.desc()).limit(1)
    ).scalars().first()
    if row is None:
        return None
    return row.close_raw * float(row.adj_factor), row.trade_date


def _owned_portfolio(session: Session, pid: int, user_id: int) -> TradePortfolio:
    p = session.get(TradePortfolio, pid)
    if p is None or p.user_id != user_id:
        raise HTTPException(status_code=404, detail="portfolio not found")
    return p


def _default_portfolio(session: Session, user_id: int) -> TradePortfolio:
    p = session.scalar(select(TradePortfolio).where(
        TradePortfolio.user_id == user_id, TradePortfolio.kind == "manual").order_by(TradePortfolio.id))
    if p is None:
        p = TradePortfolio(user_id=user_id, name="내 계좌", kind="manual")
        session.add(p)
        session.flush()
    return p


class TransactionIn(BaseModel):
    portfolio_id: int | None = None
    kind: str = Field(pattern="^(buy|sell|deposit|withdraw)$")
    code: str | None = None
    qty: int | None = Field(default=None, gt=0)
    price: int | None = Field(default=None, gt=0)
    amount: int | None = Field(default=None, gt=0)
    executed_at: datetime
    memo: str | None = None
    tags: list[str] = []


@router.post("/positions", status_code=201)
def register_transaction(body: TransactionIn, user_id: int = Depends(current_user_id),
                         session: Session = Depends(get_session)) -> dict:
    pf = (_owned_portfolio(session, body.portfolio_id, user_id)
          if body.portfolio_id else _default_portfolio(session, user_id))
    inst = None
    if body.kind in ("buy", "sell"):
        if not (body.code and body.qty and body.price):
            raise HTTPException(status_code=422, detail="buy/sell requires code, qty, price")
        inst = session.scalar(select(Instrument).where(Instrument.code == body.code))
        if inst is None:
            raise HTTPException(status_code=404, detail=f"unknown code {body.code}")
    if body.kind in ("deposit", "withdraw") and not body.amount:
        raise HTTPException(status_code=422, detail="deposit/withdraw requires amount")

    realized: int | None = None
    if body.kind == "buy":
        session.add(PositionLot(portfolio_id=pf.id, instrument_id=inst.id,
                                qty_open=body.qty, price=body.price, opened_at=body.executed_at))
    elif body.kind == "sell":
        lots = session.scalars(
            select(PositionLot).where(PositionLot.portfolio_id == pf.id,
                                      PositionLot.instrument_id == inst.id).order_by(PositionLot.id)
        ).all()
        held = sum(l.qty_open for l in lots)
        if body.qty > held:
            raise HTTPException(status_code=409, detail=f"sell qty {body.qty} exceeds holding {held}")
        remaining, realized = body.qty, 0
        for l in lots:  # FIFO (feature-portfolio §5 — 전략·백테스트와 동일 회계)
            if remaining <= 0:
                break
            take = min(l.qty_open, remaining)
            realized += (body.price - l.price) * take
            l.qty_open -= take
            remaining -= take
            if l.qty_open == 0:
                session.delete(l)

    tx = TradeTransaction(portfolio_id=pf.id, kind=body.kind,
                          instrument_id=inst.id if inst else None,
                          qty=body.qty, price=body.price, amount=body.amount,
                          realized_pnl=realized, executed_at=body.executed_at,
                          memo=body.memo, tags=body.tags)
    session.add(tx)
    session.commit()
    return {"id": tx.id, "portfolio_id": pf.id, "realized_pnl": realized}


class MetaIn(BaseModel):
    target_price: int | None = None
    stop_price: int | None = None


@router.put("/portfolios/{pid}/meta/{code}")
def set_position_meta(pid: int, code: str, body: MetaIn, user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    pf = _owned_portfolio(session, pid, user_id)
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown code")
    meta = session.scalar(select(PositionMeta).where(
        PositionMeta.portfolio_id == pf.id, PositionMeta.instrument_id == inst.id))
    if meta is None:
        meta = PositionMeta(portfolio_id=pf.id, instrument_id=inst.id)
        session.add(meta)
    meta.target_price = body.target_price
    meta.stop_price = body.stop_price
    session.commit()
    return {"saved": code}


@router.post("/portfolios/from-backtest/{bt_id}", status_code=201)
def create_from_backtest(bt_id: int, user_id: int = Depends(current_user_id),
                         session: Session = Depends(get_session)) -> dict:
    bt = session.get(Backtest, bt_id)
    if bt is None or bt.user_id != user_id:
        raise HTTPException(status_code=404, detail="backtest not found")
    pf = TradePortfolio(user_id=user_id, name=f"실전 (백테스트 #{bt_id})",
                        kind="from_backtest", backtest_id=bt_id, params=bt.params)
    session.add(pf)
    from app.dashboard import record_event
    record_event(session, "portfolio_created_from_backtest", user_id)
    session.commit()
    return {"id": pf.id, "name": pf.name, "backtest_id": bt_id}


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioIn, user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """실전매매 포트 추가 — 여러 실전매매 동시 진행 (2026-08-28 지시)."""
    pf = TradePortfolio(user_id=user_id, name=body.name, kind="manual")
    session.add(pf)
    session.commit()
    return {"id": pf.id, "name": pf.name}


@router.delete("/portfolios/{pid}")
def delete_portfolio(pid: int, user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """실전매매 포트 삭제 — 거래·로트·목표/손절 기록까지 함께 제거 (되돌릴 수 없음)."""
    pf = _owned_portfolio(session, pid, user_id)
    session.query(PositionMeta).filter(PositionMeta.portfolio_id == pf.id).delete()
    session.query(PositionLot).filter(PositionLot.portfolio_id == pf.id).delete()
    session.query(TradeTransaction).filter(TradeTransaction.portfolio_id == pf.id).delete()
    session.delete(pf)
    session.commit()
    return {"deleted": pid}


@router.get("/portfolios")
def list_portfolios(user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    rows = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)).all()
    return {"items": [{"id": r.id, "name": r.name, "kind": r.kind, "backtest_id": r.backtest_id} for r in rows]}


@router.get("/portfolio/summary")
def portfolio_summary(portfolio_id: int | None = None, include_costs: bool = True,
                      user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    pf = (_owned_portfolio(session, portfolio_id, user_id)
          if portfolio_id else _default_portfolio(session, user_id))
    session.commit()  # default 생성 확정

    txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pf.id)
                          .order_by(TradeTransaction.executed_at, TradeTransaction.id)).all()
    lots = session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pf.id)
                           .order_by(PositionLot.id)).all()

    # 현금 원장
    cash = 0
    realized_total = 0
    for t in txs:
        if t.kind == "deposit":
            cash += t.amount
        elif t.kind == "withdraw":
            cash -= t.amount
        elif t.kind == "buy":
            cash -= t.qty * t.price
        elif t.kind == "sell":
            cash += t.qty * t.price
            realized_total += t.realized_pnl or 0

    # 포지션 카드
    positions = []
    total_value = 0.0
    as_of: date | None = None
    by_inst: dict[int, list[PositionLot]] = {}
    for l in lots:
        by_inst.setdefault(l.instrument_id, []).append(l)
    now = datetime.now(timezone.utc)
    for inst_id, ls in by_inst.items():
        inst = session.get(Instrument, inst_id)
        qty = sum(l.qty_open for l in ls)
        invested = sum(l.qty_open * l.price for l in ls)
        avg = invested / qty if qty else 0
        px_row = latest_close(session, inst_id)
        price, px_date = px_row if px_row else (avg, None)
        as_of = max(as_of, px_date) if (as_of and px_date) else (px_date or as_of)
        value = qty * price
        total_value += value
        ret = (price - avg) / avg if avg else 0.0
        first_buy = min(l.opened_at for l in ls)
        held_days = max((now - first_buy).days, 0)
        annualized = ((1 + ret) ** (365.0 / held_days) - 1) if held_days >= ANNUALIZE_MIN_DAYS and avg else None
        meta = session.scalar(select(PositionMeta).where(
            PositionMeta.portfolio_id == pf.id, PositionMeta.instrument_id == inst_id))
        # 최고·최저 도달 수익률 — 매수 이후 일별 종가 기준
        hist = session.execute(
            select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst_id,
                                     OhlcvDaily.trade_date >= first_buy.date())
            .order_by(OhlcvDaily.trade_date)
        ).scalars().all()
        closes = [r.close_raw * float(r.adj_factor) for r in hist]
        best = max(((c - avg) / avg for c in closes), default=ret) if avg else 0.0
        worst = min(((c - avg) / avg for c in closes), default=ret) if avg else 0.0
        positions.append({
            "code": inst.code, "name": inst.name, "qty": qty, "avg_price": round(avg),
            "price": round(price), "value": round(value),
            "return": ret, "unrealized": round(value - invested),
            "held_days": held_days, "annualized": annualized,
            "best_return": best, "worst_return": worst,
            "target_price": meta.target_price if meta else None,
            "stop_price": meta.stop_price if meta else None,
        })

    # 비용 포함/제외 토글 — v1: 수수료 0.015% 가정 추정치 (표기용)
    est_cost = sum(t.qty * t.price for t in txs if t.kind in ("buy", "sell")) * 0.00015
    total_equity = cash + total_value

    # TWR / XIRR — 입출금 현금흐름 기반
    flows = [(t.executed_at.date(), (1 if t.kind == "deposit" else -1) * t.amount)
             for t in txs if t.kind in ("deposit", "withdraw")]
    xirr_val = None
    if flows:
        cfs = [(d, -f) for d, f in flows]  # 입금 = 투자(−)
        cfs.append((date.today(), total_equity))
        xirr_val = xirr(sorted(cfs, key=lambda x: x[0]))
    twr_val = _compute_twr(session, pf.id, txs)

    return {
        "portfolio": {"id": pf.id, "name": pf.name, "kind": pf.kind, "backtest_id": pf.backtest_id},
        "as_of": as_of.isoformat() if as_of else None, "delayed": True,
        "cash": cash, "stock_value": round(total_value), "total_equity": round(total_equity),
        "realized_pnl": realized_total,
        "unrealized_pnl": round(sum(p["unrealized"] for p in positions)),
        "estimated_costs": round(est_cost) if include_costs else 0,
        "twr": twr_val, "xirr": xirr_val,
        "positions": positions,
    }


def _compute_twr(session: Session, pid: int, txs: list[TradeTransaction]) -> float | None:
    """일별 평가액 체인 재구성 — 거래일별 보유수량 × 종가 + 현금."""
    if not txs:
        return None
    start = min(t.executed_at for t in txs).date()
    inst_ids = {t.instrument_id for t in txs if t.instrument_id}
    price_map: dict[int, dict[date, float]] = {}
    all_dates: set[date] = set()
    for iid in inst_ids:
        rows = session.execute(
            select(OhlcvDaily).where(OhlcvDaily.instrument_id == iid, OhlcvDaily.trade_date >= start)
            .order_by(OhlcvDaily.trade_date)).scalars().all()
        price_map[iid] = {r.trade_date: r.close_raw * float(r.adj_factor) for r in rows}
        all_dates.update(price_map[iid])
    if not all_dates:
        return None
    days = sorted(all_dates)
    daily: list[tuple[date, float, float]] = []
    cash = 0.0
    qty: dict[int, int] = {}
    last_px: dict[int, float] = {}
    tx_iter = iter(txs)
    tx = next(tx_iter, None)
    for d in days:
        flow = 0.0
        while tx is not None and tx.executed_at.date() <= d:
            if tx.kind == "deposit":
                cash += tx.amount
                flow += tx.amount
            elif tx.kind == "withdraw":
                cash -= tx.amount
                flow -= tx.amount
            elif tx.kind == "buy":
                cash -= tx.qty * tx.price
                qty[tx.instrument_id] = qty.get(tx.instrument_id, 0) + tx.qty
            elif tx.kind == "sell":
                cash += tx.qty * tx.price
                qty[tx.instrument_id] = qty.get(tx.instrument_id, 0) - tx.qty
            tx = next(tx_iter, None)
        value = cash
        for iid, q in qty.items():
            px = price_map.get(iid, {}).get(d)
            if px is not None:
                last_px[iid] = px
            value += q * last_px.get(iid, 0.0)
        daily.append((d, value, flow))
    return twr(daily)
