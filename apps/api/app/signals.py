"""일일 시그널 서비스 + API — 전략 코드 단일 소스 (ADR-005).

시그널은 전체 이력을 백테스트 엔진으로 재계산한 마지막 계획(plan)이다 —
같은 코드 경로이므로 "백테스트 d일 절단 = 시그널 d일 주문표" 동일성이 구조적으로 성립한다.
모델 포트폴리오: 기본 자본(1억)으로 시딩 시작일부터 전략을 따라온 가상 포트.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import OrderSheetRow, SignalSnapshot
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

router = APIRouter()

MODEL_CAPITAL = 100_000_000  # 모델 포트 기본 자본 (표시용 — 수량 산출 기준)


def run_signal_batch(session: Session, target: date | None = None) -> SignalSnapshot:
    """시그널 배치 — append-only 버전 기록. 실패도 스냅샷으로 남긴다 (조용한 실패 금지)."""
    from app.backtests import load_aligned_bars

    try:
        bars_200, bars_lev, fp = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
    except Exception as exc:
        return _record(session, target or date.today(), "MISSING", detail={"error": str(exc)[:500]})

    signal_date = date.fromisoformat(bars_200[-1]["date"])  # 최신 종가 확정일
    if target is not None and signal_date < target:
        # 대상일 시세 미확보 → 발행 보류 (feature-strategy-engine §5.8)
        return _record(session, target, "MISSING",
                       detail={"last_bar": bars_200[-1]["date"], "reason": "market data not ingested yet"})
    try:
        result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params(),
                              collect_plans=True, plan_final=True)
        last_plan = result.plans[-1]
    except Exception as exc:
        return _record(session, signal_date, "FAILED", detail={"error": str(exc)[:500]})

    snap = _record(
        session, signal_date,
        last_plan.status if last_plan.status != "OK" else "OK",
        regime=last_plan.regime.value, e=last_plan.e_target, w200=last_plan.w_200, wlev=last_plan.w_lev,
        gap=last_plan.gap_cancel_below, indicators=last_plan.indicators,
        detail={
            "model_capital": MODEL_CAPITAL,
            "model_equity": round(result.equity[-1]) if result.equity else MODEL_CAPITAL,
            "model_cash": round(result.cash_curve[-1]) if result.cash_curve else MODEL_CAPITAL,
            "model_qty_200": result.qty_200[-1] if result.qty_200 else 0,
            "model_qty_lev": result.qty_lev[-1] if result.qty_lev else 0,
            "plans": len(result.plans),
        },
    )
    for od in last_plan.orders:
        session.add(OrderSheetRow(signal_id=snap.id, instrument=od.instrument, side=od.side,
                                  otype=od.otype, qty=od.qty, price=od.price, kind=od.kind))
    session.commit()
    return snap


def _record(session: Session, trade_date: date, status: str, regime=None, e=None, w200=None,
            wlev=None, gap=None, indicators=None, detail=None) -> SignalSnapshot:
    prev = session.scalars(
        select(SignalSnapshot).where(SignalSnapshot.trade_date == trade_date)
    ).all()
    for s in prev:
        s.is_current = False
    snap = SignalSnapshot(
        trade_date=trade_date, version=len(prev) + 1, is_current=True, status=status,
        regime=regime, e_target=e, w_200=w200, w_lev=wlev, gap_cancel_below=gap,
        indicators={k: v for k, v in (indicators or {}).items() if v is not None},
        detail=detail or {},
    )
    session.add(snap)
    session.flush()
    return snap


def _portfolio_orders(session: Session, pid: int, user_id: int) -> dict:
    """내 실전 포트 기준 주문표 — 보유 로트·현금을 플래너 Portfolio 로 변환해 plan() 직접 실행 (ADR-005).

    근사 규칙(ASSUMPTIONS): 실전 로트의 익절가는 '오늘 Grid' 기준 매수가×(1+Grid)로 부여,
    상승장이면 코어로 간주. 200 ETF 는 KODEX/TIGER 모두 K200 레그로 매핑.
    """
    from app.backtests import load_aligned_bars
    from app.models import Instrument, PositionLot, TradePortfolio, TradeTransaction
    from app.strategy.planner import K200, LEV, Lot, Portfolio, grid_ratio, plan, prepare
    from app.strategy.params import round_tick
    from app.strategy.regime import Regime

    pf_row = session.get(TradePortfolio, pid)
    if pf_row is None or pf_row.user_id != user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="portfolio not found")

    bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
    params = Params()
    result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, params)
    regime = Regime(result.regimes[-1])  # 시장 레짐은 가격만의 함수 — 포트와 무관

    def to_market(bars):
        return prepare([float(b["open"]) for b in bars], [float(b["high"]) for b in bars],
                       [float(b["low"]) for b in bars], [float(b["close"]) for b in bars], params)

    m200, mlev = to_market(bars_200), to_market(bars_lev)
    last = len(bars_200) - 1
    grid_today = grid_ratio(m200.atr20[last], m200.closes[last], params)

    # 현금 원장
    txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pid)).all()
    cash = 0
    for t in txs:
        if t.kind == "deposit":
            cash += t.amount
        elif t.kind == "withdraw":
            cash -= t.amount
        elif t.kind == "buy":
            cash -= t.qty * t.price
        elif t.kind == "sell":
            cash += t.qty * t.price

    lots_rows = session.scalars(
        select(PositionLot).where(PositionLot.portfolio_id == pid)
        .order_by(PositionLot.opened_at, PositionLot.id)  # FIFO 결정론 (검증 ①⑧)
    ).all()
    lots: list[Lot] = []
    qty_200 = qty_lev = 0
    SUPPORTED = {"069500", "102110", "122630"}
    for l in lots_rows:
        code = session.get(Instrument, l.instrument_id).code
        if code not in SUPPORTED:
            from fastapi import HTTPException
            raise HTTPException(status_code=409,
                                detail=f"전략 대상 외 종목({code}) 보유 — 이 포트 기준 주문표를 계산할 수 없습니다")
        if code == "122630":
            lots.append(Lot(LEV, l.qty_open, l.price, "lev_strat", None, 0))
            qty_lev += l.qty_open
        else:  # 069500 / 102110 → 200 레그
            if regime is Regime.BULL and params.flags.f1_no_tp_in_bull:
                lots.append(Lot(K200, l.qty_open, l.price, "core", None, 0))
            else:
                # 익절 기준가 = 최근 종가 × (1+오늘 Grid) — 정본 §5.6 코어 편입 규칙 준용.
                # 평단 기준으로 하면 과거 매수분이 "이미 목표 도달"로 시작 즉시 전량 매도됨 (2026-08-28 검토)
                tp = round_tick(m200.closes[last] * (1 + grid_today), params.tick, up=True)
                lots.append(Lot(K200, l.qty_open, l.price, "grid", tp, 0))
            qty_200 += l.qty_open

    user_pf = Portfolio(cash=float(cash), lots=lots)
    p = plan(last, m200, mlev, regime, user_pf, params)
    return {
        "basis": "portfolio", "portfolio": {"id": pf_row.id, "name": pf_row.name},
        "account": {"cash": cash, "qty_200": qty_200, "qty_lev": qty_lev,
                    "equity": round(user_pf.equity(m200.closes[last], mlev.closes[last]))},
        "orders": [
            {"instrument": o.instrument, "side": o.side, "otype": o.otype,
             "qty": o.qty, "price": o.price, "kind": o.kind} for o in p.orders
        ],
        "gap_cancel_below": p.gap_cancel_below,
    }


@router.get("/signals/journal")
def get_signal_journal(days: int = 20, _user: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """모델 포트의 최근 매매 이력 — 주문표 신호의 맥락 (계획·체결·수익률·보유)."""
    from app.backtests import load_aligned_bars

    try:
        bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
    except Exception:
        return {"items": []}
    r = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params(), collect_plans=True)
    fills_by_date: dict[str, list] = {}
    for f in r.fills:
        fills_by_date.setdefault(f.date, []).append(
            {"instrument": f.instrument, "side": f.side, "kind": f.kind, "price": f.price, "qty": f.qty})
    items = []
    n = len(r.dates)
    for i in range(max(0, n - min(days, 120)), n):
        plan_i = r.plans[i] if i < len(r.plans) else None
        prev_eq = r.equity[i - 1] if i > 0 else MODEL_CAPITAL
        eq_r, prev_r = round(r.equity[i]), round(prev_eq)
        items.append({
            "date": r.dates[i], "regime": r.regimes[i],
            "equity": eq_r,
            "day_return": (r.equity[i] / prev_eq - 1.0) if prev_eq else 0.0,
            "day_pnl": eq_r - prev_r,
            "qty_200": r.qty_200[i], "qty_lev": r.qty_lev[i], "cash": round(r.cash_curve[i]),
            "planned": [
                {"instrument": o.instrument, "side": o.side, "kind": o.kind, "price": o.price, "qty": o.qty}
                for o in (plan_i.orders if plan_i and plan_i.status == "OK" else [])
            ],
            "fills": fills_by_date.get(r.dates[i], []),
        })
    items.reverse()
    return {"items": items}


@router.get("/signals/daily")
def get_daily_signal(date_: date | None = Query(default=None, alias="date"),
                     portfolio_id: int | None = None,
                     _user: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    q = select(SignalSnapshot).where(SignalSnapshot.is_current)
    if date_ is not None:
        q = q.where(SignalSnapshot.trade_date == date_)
    snap = session.scalars(q.order_by(SignalSnapshot.trade_date.desc()).limit(1)).first()
    if snap is None:
        return {"status": "MISSING", "reason": "시그널 배치가 아직 실행되지 않았습니다 — 시세 시딩 후 배치를 실행하세요"}
    orders = session.scalars(
        select(OrderSheetRow).where(OrderSheetRow.signal_id == snap.id).order_by(OrderSheetRow.id)
    ).all()
    extra: dict = {"basis": "model"}
    if portfolio_id is not None and snap.status == "OK":
        # 내 실전 포트 기준 주문표 (2026-08-28 검토 반영) — 주문·계좌 현황을 내 포트 기준으로 교체
        extra = _portfolio_orders(session, portfolio_id, _user)
        return {
            "status": snap.status, "trade_date": snap.trade_date.isoformat(), "version": snap.version,
            "regime": snap.regime, "e_target": float(snap.e_target) if snap.e_target is not None else None,
            "w_200": float(snap.w_200) if snap.w_200 is not None else None,
            "w_lev": float(snap.w_lev) if snap.w_lev is not None else None,
            "gap_cancel_below": extra["gap_cancel_below"] or snap.gap_cancel_below,
            "indicators": snap.indicators, "detail": snap.detail,
            **extra,
        }
    return {
        "status": snap.status, "trade_date": snap.trade_date.isoformat(), "version": snap.version,
        "regime": snap.regime, "e_target": float(snap.e_target) if snap.e_target is not None else None,
        "w_200": float(snap.w_200) if snap.w_200 is not None else None,
        "w_lev": float(snap.w_lev) if snap.w_lev is not None else None,
        "gap_cancel_below": snap.gap_cancel_below,
        "indicators": snap.indicators, "detail": snap.detail,
        "orders": [
            {"instrument": o.instrument, "side": o.side, "otype": o.otype,
             "qty": o.qty, "price": o.price, "kind": o.kind} for o in orders
        ],
        **extra,
    }


@router.get("/signals/history")
def get_signal_history(_user: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """레짐·노출 이력 — 저장 스냅샷이 아니라 전략 재계산(결정론)으로 전체 구간 제공."""
    try:
        from app.backtests import load_aligned_bars

        bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
        result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params())
        return {"items": [
            {"date": d, "regime": r, "exposure": e}
            for d, r, e in zip(result.dates, result.regimes, result.exposures)
        ]}
    except Exception:
        return {"items": []}
