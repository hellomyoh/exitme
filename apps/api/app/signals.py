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


@router.get("/signals/daily")
def get_daily_signal(date_: date | None = Query(default=None, alias="date"),
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
