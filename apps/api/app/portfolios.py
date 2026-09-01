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

    lo, hi = -0.999999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    while f_lo * f_hi > 0 and hi < 1e6:  # 초고수익 단기 흐름 대응 — 브래킷 확장 (검증 M-1)
        hi *= 10.0
        f_hi = npv(hi)
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

    기시흐름 규약: 수익률_t = V_t / (V_{t−1} + F_t) — 흐름은 당일 수익 계산의 분모에
    들어가며(입금 당일 성과 반영), 첫날도 (0 + F_0) 분모로 포함한다 (검증 H-1·H-2).
    분모 ≤ 0 인 날은 건너뛴다.
    """
    if not daily:
        return None
    acc = 1.0
    prev_v = 0.0
    linked = False
    for _, v, f in daily:
        denom = prev_v + f
        if denom > 0:
            acc *= v / denom
            linked = True
        prev_v = v
    return acc - 1.0 if linked else None


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
        code_market = "US" if inst.market == "NASDAQ" else "KR"
        if code_market != pf.market:
            raise HTTPException(status_code=409,
                                detail=f"{body.code} 는 {code_market} 종목 — {pf.market} 포트에 등록할 수 없습니다 (통화 혼합 방지)")
    if body.kind in ("deposit", "withdraw") and not body.amount:
        raise HTTPException(status_code=422, detail="deposit/withdraw requires amount")
    if body.kind == "withdraw":
        # 현금 잔고 초과 출금 방지 — 음수 현금은 TWR/평가를 왜곡 (검증 M-4)
        cash_now = 0
        for t in session.scalars(select(TradeTransaction).where(
                TradeTransaction.portfolio_id == pf.id)).all():
            if t.kind == "deposit":
                cash_now += t.amount
            elif t.kind == "withdraw":
                cash_now -= t.amount
            elif t.kind == "buy":
                cash_now -= t.qty * t.price
            elif t.kind == "sell":
                cash_now += t.qty * t.price
        if body.amount > cash_now:
            raise HTTPException(status_code=409,
                                detail=f"withdraw {body.amount} exceeds cash {cash_now}")

    realized: int | None = None
    if body.kind == "buy":
        session.add(PositionLot(portfolio_id=pf.id, instrument_id=inst.id,
                                qty_open=body.qty, price=body.price, opened_at=body.executed_at))
    elif body.kind == "sell":
        lots = session.scalars(
            select(PositionLot).where(PositionLot.portfolio_id == pf.id,
                                      PositionLot.instrument_id == inst.id)
            .order_by(PositionLot.opened_at, PositionLot.id)  # 취득 시각 FIFO (검증 H-3)
        ).all()
        # 매도 시점 이후 취득 로트는 매도 대상이 아님 — 소급 입력 시 원장 왜곡 방지 (검증 H-4)
        lots = [l for l in lots if l.opened_at <= body.executed_at]
        held = sum(l.qty_open for l in lots)
        if body.qty > held:
            raise HTTPException(status_code=409,
                                detail=f"sell qty {body.qty} exceeds holding {held} as of executed_at")
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


def _rebuild_ledger(session: Session, pf_id: int) -> None:
    """포트의 로트·실현손익을 거래 재생으로 재구성 — 거래 삭제(오입력 정정) 후 호출 (2026-08-29).

    등록 시점에 로트를 직접 변형하는 구조라, 과거 거래를 지우면 역산이 아니라
    남은 거래 전체를 시간순으로 다시 재생하는 것이 유일하게 안전하다.
    재생 불가능(매도가 보유 초과, 현금 음수)이면 409 로 거부한다.
    """
    txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pf_id)
                          .order_by(TradeTransaction.executed_at, TradeTransaction.id)).all()
    session.query(PositionLot).filter(PositionLot.portfolio_id == pf_id).delete()
    session.flush()
    cash = 0
    fifo: dict[int, list[PositionLot]] = {}
    for t in txs:
        if t.kind == "deposit":
            cash += t.amount
        elif t.kind == "withdraw":
            cash -= t.amount
            if cash < 0:
                raise HTTPException(status_code=409,
                                    detail=f"삭제 후 {t.executed_at.date()} 출금이 현금 잔고를 초과합니다 — 해당 출금을 먼저 삭제하세요")
        elif t.kind == "buy":
            cash -= t.qty * t.price
            lot = PositionLot(portfolio_id=pf_id, instrument_id=t.instrument_id,
                              qty_open=t.qty, price=t.price, opened_at=t.executed_at)
            session.add(lot)
            fifo.setdefault(t.instrument_id, []).append(lot)
        elif t.kind == "sell":
            cash += t.qty * t.price
            lots = [l for l in fifo.get(t.instrument_id, []) if l.qty_open > 0 and l.opened_at <= t.executed_at]
            held = sum(l.qty_open for l in lots)
            if t.qty > held:
                raise HTTPException(status_code=409,
                                    detail=f"삭제 후 {t.executed_at.date()} 매도({t.qty}주)가 보유({held}주)를 초과합니다 — 이 매도를 먼저 삭제하세요")
            remaining, realized = t.qty, 0
            for l in lots:
                if remaining <= 0:
                    break
                take = min(l.qty_open, remaining)
                realized += (t.price - l.price) * take
                l.qty_open -= take
                remaining -= take
            t.realized_pnl = realized
    # 잔여 0 로트 정리
    for lots in fifo.values():
        for l in lots:
            if l.qty_open == 0:
                session.delete(l)
    session.flush()


@router.delete("/positions/{tx_id}")
def delete_transaction(tx_id: int, user_id: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """거래 삭제 — 오입력 정정용. 남은 거래를 재생해 로트·실현손익·현금을 재구성한다 (2026-08-29 지시)."""
    tx = session.get(TradeTransaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    pf = _owned_portfolio(session, tx.portfolio_id, user_id)
    session.delete(tx)
    session.flush()
    _rebuild_ledger(session, pf.id)
    session.commit()
    return {"deleted": tx_id}


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
    """백테스트 → 실전 전환 — 종료 시점 상태(현금·보유 로트)를 시드해 그대로 이어서 운영 (2026-08-28 지시).

    입금(현금+보유 원가 합) + 로트별 매수 거래(원 체결가·일자)로 등록된다. 과거 실현손익 이력은 이관하지 않는다.
    """
    from datetime import time as _time, timedelta as _td, timezone as _tz

    from app.backtests import load_bars_with_warmup, market_of_etf, pair_from_params, params_from_job, run_engine
    from app.strategy.backtest import run_backtest

    bt = session.get(Backtest, bt_id)
    if bt is None or bt.user_id != user_id:
        raise HTTPException(status_code=404, detail="backtest not found")
    if bt.status != "DONE":
        raise HTTPException(status_code=409, detail=f"cannot convert job in status {bt.status}")

    p = bt.params
    code_200, code_lev = pair_from_params(p)
    bars_200, bars_lev, _fp, start_idx = load_bars_with_warmup(
        session, date.fromisoformat(p["date_from"]), date.fromisoformat(p["date_to"]),
        codes=(code_200, code_lev))
    params = params_from_job(p)
    result = run_engine(p, bars_200, bars_lev, float(p["capital"]), params, start_index=start_idx)

    pf = TradePortfolio(user_id=user_id, name=f"실전 (백테스트 #{bt_id})",
                        kind="from_backtest", backtest_id=bt_id, params=bt.params,
                        market=market_of_etf(p.get("etf", "KODEX")))
    session.add(pf)
    session.flush()

    kst = _tz(_td(hours=9))
    end_dt = datetime.combine(date.fromisoformat(p["date_to"]), _time(15, 30), tzinfo=kst)
    cash = round(result.cash_curve[-1]) if result.cash_curve else int(p["capital"])
    lots_cost = sum(l["qty"] * l["price"] for l in result.final_lots)
    session.add(TradeTransaction(portfolio_id=pf.id, kind="deposit", amount=cash + lots_cost,
                                 executed_at=end_dt, memo=f"백테스트 #{bt_id} 전환 시드"))
    code_map = {"K200": code_200, "LEV": code_lev}
    seeded = 0
    for l in result.final_lots:
        inst = session.scalar(select(Instrument).where(Instrument.code == code_map[l["instrument"]]))
        buy_dt = datetime.combine(date.fromisoformat(l["date"]), _time(15, 30), tzinfo=kst)
        session.add(PositionLot(portfolio_id=pf.id, instrument_id=inst.id,
                                qty_open=l["qty"], price=l["price"], opened_at=buy_dt))
        session.add(TradeTransaction(portfolio_id=pf.id, kind="buy", instrument_id=inst.id,
                                     qty=l["qty"], price=l["price"], executed_at=buy_dt,
                                     memo="백테스트 보유분 이관"))
        seeded += 1

    from app.dashboard import record_event
    record_event(session, "portfolio_created_from_backtest", user_id)
    session.commit()
    return {"id": pf.id, "name": pf.name, "backtest_id": bt_id,
            "seeded_cash": cash, "seeded_lots": seeded}


@router.get("/portfolio/transactions")
def list_transactions(portfolio_id: int | None = None, limit: int = 500,
                      user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    """거래 내역 — 날짜별 그룹은 클라이언트에서 (시뮬레이터 저널과 동일 UX)."""
    pf = (_owned_portfolio(session, portfolio_id, user_id)
          if portfolio_id else _default_portfolio(session, user_id))
    session.commit()
    txs = session.scalars(
        select(TradeTransaction).where(TradeTransaction.portfolio_id == pf.id)
        .order_by(TradeTransaction.executed_at.desc(), TradeTransaction.id.desc()).limit(min(limit, 2000))
    ).all()
    code_cache: dict[int, Instrument] = {}
    def inst_of(iid):
        if iid not in code_cache:
            code_cache[iid] = session.get(Instrument, iid)
        return code_cache[iid]
    return {"portfolio_id": pf.id, "items": [
        {"id": t.id, "kind": t.kind,
         "code": inst_of(t.instrument_id).code if t.instrument_id else None,
         "name": inst_of(t.instrument_id).name if t.instrument_id else None,
         "qty": t.qty, "price": t.price, "amount": t.amount,
         "realized_pnl": t.realized_pnl,
         "executed_at": t.executed_at.isoformat(), "memo": t.memo, "tags": t.tags}
        for t in txs
    ]}


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    market: str = Field(default="KR", pattern="^(KR|US)$")
    code_200: str | None = Field(default=None, pattern="^(069500|102110)$")  # KR 주력 ETF (기본 TIGER, 2026-09-01)


@router.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioIn, user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """실전매매 포트 추가 — 여러 실전매매 동시 진행 (2026-08-28 지시)."""
    pf = TradePortfolio(user_id=user_id, name=body.name, kind="manual", market=body.market,
                        params={"code_200": body.code_200} if body.code_200 else None)
    session.add(pf)
    session.commit()
    return {"id": pf.id, "name": pf.name, "market": pf.market}


@router.delete("/portfolios/{pid}")
def delete_portfolio(pid: int, user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """실전매매 포트 삭제 — 거래·로트·목표/손절 기록까지 함께 제거 (되돌릴 수 없음)."""
    from app.models import PortfolioPlan

    pf = _owned_portfolio(session, pid, user_id)
    session.query(PortfolioPlan).filter(PortfolioPlan.portfolio_id == pf.id).delete()  # 계획 스냅샷 (0008)
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
    return {"items": [{"id": r.id, "name": r.name, "kind": r.kind, "backtest_id": r.backtest_id,
                       "market": r.market} for r in rows]}


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
        # 최고·최저 도달 수익률 — 시점별 평단(FIFO 재생) 대비 그날 종가 (검증 H-5:
        # 현재 평단을 과거 전 구간에 적용하면 부분 매도 이력이 있는 종목이 왜곡됨)
        inst_txs = [t for t in txs if t.instrument_id == inst_id and t.kind in ("buy", "sell")]
        events: list[tuple[date, float | None]] = []
        fifo: list[list[int]] = []
        for t in inst_txs:
            if t.kind == "buy":
                fifo.append([t.qty, t.price])
            else:
                rem = t.qty
                while rem > 0 and fifo:
                    take = min(fifo[0][0], rem)
                    fifo[0][0] -= take
                    rem -= take
                    if fifo[0][0] == 0:
                        fifo.pop(0)
            q_ev = sum(x[0] for x in fifo)
            events.append((t.executed_at.date(),
                           (sum(x[0] * x[1] for x in fifo) / q_ev) if q_ev else None))
        hist_from = inst_txs[0].executed_at.date() if inst_txs else first_buy.date()
        hist = session.execute(
            select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst_id,
                                     OhlcvDaily.trade_date >= hist_from)
            .order_by(OhlcvDaily.trade_date)
        ).scalars().all()
        best = worst = ret
        ei = 0
        cur_avg: float | None = None
        for r in hist:
            while ei < len(events) and events[ei][0] <= r.trade_date:
                cur_avg = events[ei][1]
                ei += 1
            if cur_avg:
                rr = (r.close_raw * float(r.adj_factor) - cur_avg) / cur_avg
                best = max(best, rr)
                worst = min(worst, rr)
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
        from app.dashboard import kst_today
        cfs.append((kst_today(), total_equity))
        xirr_val = xirr(sorted(cfs, key=lambda x: x[0]))
    twr_val = _compute_twr(session, pf.id, txs)

    return {
        "portfolio": {"id": pf.id, "name": pf.name, "kind": pf.kind, "backtest_id": pf.backtest_id,
                      "market": pf.market},
        "as_of": as_of.isoformat() if as_of else None, "delayed": True,
        "cash": cash, "stock_value": round(total_value), "total_equity": round(total_equity),
        "realized_pnl": realized_total,
        "unrealized_pnl": round(sum(p["unrealized"] for p in positions)),
        "estimated_costs": round(est_cost) if include_costs else 0,
        "twr": twr_val, "xirr": xirr_val,
        "positions": positions,
    }


def _daily_series(session: Session, pid: int, txs: list[TradeTransaction]) -> list[tuple[date, float, float]]:
    """일별 (일자, 종가 평가액, 외부 현금흐름) 재구성 — TWR·수익률 그래프 공용."""
    if not txs:
        return []
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
        return []
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
                last_px.setdefault(tx.instrument_id, float(tx.price))  # 시세 결측 폴백 (검증 C-1)
            elif tx.kind == "sell":
                cash += tx.qty * tx.price
                qty[tx.instrument_id] = qty.get(tx.instrument_id, 0) - tx.qty
            tx = next(tx_iter, None)
        if cash < 0:
            # 입금 기록 없이 매수한 자본 = 암묵 외부 유입 — 흐름으로 계상해 TWR 왜곡 방지 (검증 C-2)
            flow += -cash
            cash = 0.0
        value = cash
        for iid, q in qty.items():
            px = price_map.get(iid, {}).get(d)
            if px is not None:
                last_px[iid] = px
            value += q * last_px.get(iid, 0.0)
        daily.append((d, value, flow))
    return daily


def _compute_twr(session: Session, pid: int, txs: list[TradeTransaction]) -> float | None:
    return twr(_daily_series(session, pid, txs))


@router.get("/portfolio/equity")
def portfolio_equity(portfolio_id: int | None = None,
                     user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """실전 포트 수익률 곡선 — TWR 지수(시작=100, 입출금 왜곡 제거) + 평가액 (2026-08-28 지시)."""
    pf = (_owned_portfolio(session, portfolio_id, user_id)
          if portfolio_id else _default_portfolio(session, user_id))
    session.commit()
    txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pf.id)
                          .order_by(TradeTransaction.executed_at, TradeTransaction.id)).all()
    daily = _daily_series(session, pf.id, txs)
    items = []
    index = 100.0
    prev_v = 0.0
    for d, v, f in daily:
        denom = prev_v + f  # 기시흐름 규약 — twr() 와 동일 (검증 H-1)
        if denom > 0:
            index *= v / denom
        items.append({"date": d.isoformat(), "equity": round(v), "index": round(index, 4)})
        prev_v = v
    return {"portfolio_id": pf.id, "items": items}


@router.get("/portfolio/journal")
def portfolio_journal(portfolio_id: int | None = None, days: int = 60,
                      user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    """일자별 매매 일지 — 그날의 주문표(계획 스냅샷) + 체결(등록 거래) + 일간 수익률 (2026-08-29 지시).

    시뮬레이터 저널과 동일한 형태. 계획은 주문표 조회 시점에 보존된 스냅샷(PortfolioPlan)이며,
    조회한 적 없는 과거 날짜는 계획 없이 체결·수익률만 표시된다.
    """
    from app.models import PortfolioPlan

    pf = (_owned_portfolio(session, portfolio_id, user_id)
          if portfolio_id else _default_portfolio(session, user_id))
    session.commit()
    txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pf.id)
                          .order_by(TradeTransaction.executed_at, TradeTransaction.id)).all()
    plans = session.scalars(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pf.id)).all()
    daily = _daily_series(session, pf.id, txs)

    # 일간 수익률: TWR 지수 체인과 동일 규약 (기시흐름)
    ret_by_date: dict[str, float] = {}
    eq_by_date: dict[str, float] = {}
    prev_v = 0.0
    for d, v, f in daily:
        denom = prev_v + f
        if denom > 0:
            ret_by_date[d.isoformat()] = v / denom - 1.0
        eq_by_date[d.isoformat()] = v
        prev_v = v

    tx_by_date: dict[str, list[TradeTransaction]] = {}
    for t in txs:
        tx_by_date.setdefault(t.executed_at.date().isoformat(), []).append(t)
    plan_by_date = {r.trade_date.isoformat(): r.payload for r in plans}

    code_cache: dict[int, Instrument] = {}

    def inst_of(iid):
        if iid not in code_cache:
            code_cache[iid] = session.get(Instrument, iid)
        return code_cache[iid]

    all_dates = sorted(set(tx_by_date) | set(plan_by_date), reverse=True)[: min(days, 365)]
    items = []
    for d in all_dates:
        pl = plan_by_date.get(d)
        fills = tx_by_date.get(d, [])
        realized = sum(t.realized_pnl or 0 for t in fills)
        items.append({
            "date": d,
            "regime": pl.get("regime") if pl else None,
            "planned": pl.get("orders", []) if pl else None,   # None = 스냅샷 없음(주문표 미조회일)
            "gap_cancel_below": pl.get("gap_cancel_below") if pl else None,
            "account": pl.get("account") if pl else None,       # 계획 시점 현금·보유 (하단 현황줄)
            "e_target": pl.get("e_target") if pl else None,
            "fills": [
                {"id": t.id, "kind": t.kind,
                 "code": inst_of(t.instrument_id).code if t.instrument_id else None,
                 "name": inst_of(t.instrument_id).name if t.instrument_id else None,
                 "qty": t.qty, "price": t.price, "amount": t.amount,
                 "realized_pnl": t.realized_pnl, "time": t.executed_at.isoformat()[11:16],
                 "memo": t.memo}
                for t in fills
            ],
            "realized_pnl": realized,
            "day_return": ret_by_date.get(d),
            "equity": round(eq_by_date[d]) if d in eq_by_date else None,
        })
    return {"portfolio_id": pf.id, "items": items}
