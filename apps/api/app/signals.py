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
from app.models import OrderSheetRow, SignalSnapshot, TradePortfolio
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

router = APIRouter()

MODEL_CAPITAL = 100_000_000  # 모델 포트 기본 자본 (표시용 — 수량 산출 기준)


def _next_exec_day(base_day: date) -> date:
    """신호 기준일(base_day 종가)의 실행일 — 다음 평일 (주말 스킵)."""
    from datetime import timedelta as _td

    exec_day = base_day + _td(days=1)
    while exec_day.weekday() >= 5:
        exec_day += _td(days=1)
    return exec_day


def _state_before(session: Session, pid: int, cutoff: date) -> tuple[list[dict], int]:
    """cutoff(KST 일자) 이전에 체결된 거래만으로 로트·현금을 재구성 — B안 (2026-09-02).

    주문표 = 신호 기준일 종가 시점 상태의 함수(정본 §8 "종가 신호 → 익일 발주").
    실행일 당일의 체결 등록이 당일 계획을 바꾸지 않도록 원장을 시점 재생한다.
    FIFO 의미론은 등록 경로(portfolios — opened_at ≤ 매도 시각 필터 포함)와 동일하며,
    동등성은 테스트(cutoff=미래 ↔ 현재 로트 테이블 일치)로 고정한다.
    반환: ([{instrument_id, qty, price}] 체결 시각순, 현금).
    """
    from datetime import timedelta as _td, timezone as _tz

    from app.models import TradeTransaction

    kst = _tz(_td(hours=9))
    txs = session.scalars(
        select(TradeTransaction).where(TradeTransaction.portfolio_id == pid)
        .order_by(TradeTransaction.executed_at, TradeTransaction.id)
    ).all()

    def kdate(t):
        dt = t.executed_at
        return (dt.astimezone(kst) if dt.tzinfo else dt).date()

    cash = 0
    lots: list[dict] = []
    for t in txs:
        if kdate(t) >= cutoff:
            continue
        if t.kind == "deposit":
            cash += t.amount
        elif t.kind == "withdraw":
            cash -= t.amount
        elif t.kind == "buy":
            cash -= t.qty * t.price
            lots.append({"instrument_id": t.instrument_id, "qty": t.qty,
                         "price": t.price, "opened_at": t.executed_at})
        elif t.kind == "sell":
            cash += t.qty * t.price
            remaining = t.qty
            for l in lots:
                if remaining <= 0:
                    break
                if l["instrument_id"] != t.instrument_id or l["opened_at"] > t.executed_at:
                    continue
                take = min(l["qty"], remaining)
                l["qty"] -= take
                remaining -= take
            lots = [l for l in lots if l["qty"] > 0]
    return lots, cash


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

    from app.backtests import base_costs_for, user_algo_overrides
    if pf_row.market == "US":
        # 보유 레버리지에 따라 페어 결정 (TQQQ 보유 시 3배 파라미터)
        held_codes = {
            session.get(Instrument, l.instrument_id).code
            for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all()
        }
        if "TQQQ" in held_codes and "QLD" in held_codes:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="QLD 와 TQQQ 혼합 보유 — 한 포트에는 한 레버리지만 운용하세요")
        etf = "QQQ_TQQQ" if "TQQQ" in held_codes else "QQQ_QLD"
        codes = ("QQQ", "TQQQ" if "TQQQ" in held_codes else "QLD")
    else:
        # KR: 보유 중인 200 레그 종목을 주력으로 — TIGER 보유자는 TIGER 가격 기준 주문 (2026-09-01 지시)
        held = {
            session.get(Instrument, l.instrument_id).code
            for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all()
        }
        pref = (pf_row.params or {}).get("code_200") if pf_row.params else None
        if "102110" in held and "069500" not in held:
            code_200 = "102110"
        elif "069500" in held:
            code_200 = "069500"
        else:
            code_200 = pref or "069500"  # 보유 없으면 생성 시 선택한 조합 (기존 포트는 KODEX 유지)
        etf, codes = ("TIGER" if code_200 == "102110" else "KODEX"), (code_200, "122630")
    algo = user_algo_overrides(session, user_id)
    bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1), codes=codes)
    params = Params(**{**base_costs_for(etf), **algo})
    result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, params)
    regime = Regime(result.regimes[-1])  # 시장 레짐은 가격만의 함수 — 포트와 무관

    def to_market(bars):
        return prepare([float(b["open"]) for b in bars], [float(b["high"]) for b in bars],
                       [float(b["low"]) for b in bars], [float(b["close"]) for b in bars], params)

    m200, mlev = to_market(bars_200), to_market(bars_lev)
    last = len(bars_200) - 1
    grid_today = grid_ratio(m200.atr20[last], m200.closes[last], params)
    base_day = date.fromisoformat(bars_200[last]["date"])
    exec_day = _next_exec_day(base_day)

    # 계좌 상태 = 신호 기준일 종가 시점(실행일 이전 체결만) — B안 (feature-portfolio §5, 2026-09-02).
    # 실행일 당일의 체결 등록은 당일 주문표를 바꾸지 않는다 (HTS 주문장과 화면 불일치 방지).
    lot_rows, cash = _state_before(session, pid, exec_day)
    lots: list[Lot] = []
    qty_200 = qty_lev = 0
    SUPPORTED = ({"QQQ", "QLD", "TQQQ"} if pf_row.market == "US"
                 else {"069500", "102110", "122630"})
    LEV_CODES = {"122630", "QLD", "TQQQ"}
    for l in lot_rows:
        code = session.get(Instrument, l["instrument_id"]).code
        if code not in SUPPORTED:
            from fastapi import HTTPException
            raise HTTPException(status_code=409,
                                detail=f"전략 대상 외 종목({code}) 보유 — 이 포트 기준 주문표를 계산할 수 없습니다")
        if code in LEV_CODES:
            lots.append(Lot(LEV, l["qty"], l["price"], "lev_strat", None, 0))
            qty_lev += l["qty"]
        else:  # 1배 주력(069500/102110/QQQ) → 200 레그
            if regime is Regime.BULL and params.flags.f1_no_tp_in_bull:
                lots.append(Lot(K200, l["qty"], l["price"], "core", None, 0))
            else:
                # 익절 기준가 = 최근 종가 × (1+오늘 Grid) — 정본 §5.6 코어 편입 규칙 준용.
                # 평단 기준으로 하면 과거 매수분이 "이미 목표 도달"로 시작 즉시 전량 매도됨 (2026-08-28 검토)
                tp = round_tick(m200.closes[last] * (1 + grid_today), params.tick, up=True)
                lots.append(Lot(K200, l["qty"], l["price"], "grid", tp, 0))
            qty_200 += l["qty"]

    user_pf = Portfolio(cash=float(cash), lots=lots)
    p = plan(last, m200, mlev, regime, user_pf, params)
    # 표시용 병합 — 로트별 익절이 같은 가격이면 한 주문으로 (HTS 에는 하나로 넣으면 됨, 2026-08-29 검토)
    merged: dict[tuple, dict] = {}
    for o in p.orders:
        key = (o.instrument, o.side, o.otype, o.price, o.kind)
        if key in merged:
            merged[key]["qty"] += o.qty
        else:
            merged[key] = {"instrument": o.instrument, "side": o.side, "otype": o.otype,
                           "qty": o.qty, "price": o.price, "kind": o.kind}
    out = {
        "basis": "portfolio", "portfolio": {"id": pf_row.id, "name": pf_row.name},
        "exec_day": exec_day.isoformat(),  # 이 주문표의 실행일 — 오늘/예정 표시용 (2026-09-02)
        "code_200": codes[0], "name_200": {"069500": "KODEX 200", "102110": "TIGER 200", "QQQ": "QQQ"}.get(codes[0], codes[0]),
        "account": {"cash": cash, "qty_200": qty_200, "qty_lev": qty_lev,
                    "equity": round(user_pf.equity(m200.closes[last], mlev.closes[last]))},
        "orders": list(merged.values()),
        "gap_cancel_below": p.gap_cancel_below,
    }
    # '그날의 주문표' 보존 — 일자별 매매 일지의 계획 vs 체결 대조 (2026-08-29 지시).
    # 주문표는 기준일(bars[last]) 종가 계획 = 다음 거래일 실행분이라 다음 거래일 키로 저장.
    # B안 이후 계획은 실행일 당일 체결과 무관하게 결정론적이라 upsert 갱신이 보존을 해치지 않는다.
    from app.models import PortfolioPlan
    row = session.scalar(select(PortfolioPlan).where(
        PortfolioPlan.portfolio_id == pid, PortfolioPlan.trade_date == exec_day))
    payload = {"regime": regime.value, "signal_date": base_day.isoformat(),
               "orders": out["orders"], "gap_cancel_below": p.gap_cancel_below,
               "account": out["account"], "e_target": p.e_target}
    from app.dashboard import kst_today
    if row is None:
        session.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload=payload))
    elif exec_day > kst_today():
        row.payload = payload  # 아직 실행 전 — 최신 상태로 갱신
    # 실행일 도래(오늘·과거) 계획은 불변 — "그날 아침의 계획" 보존 (2026-09-02 지시)
    session.commit()
    return out


@router.get("/signals/journal")
def get_signal_journal(days: int = 20, market: str = "KR", _user: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """모델 포트의 최근 매매 이력 — 주문표 신호의 맥락 (계획·체결·수익률·보유)."""
    from app.backtests import base_costs_for, load_aligned_bars

    try:
        if market == "US":
            from app.strategy.trendfilter import run_tf_backtest

            bars_200, _, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1),
                                               codes=("QQQ", "QQQ"))
            r = run_tf_backtest(bars_200, MODEL_CAPITAL)
        else:
            bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
            r = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params(), collect_plans=True)
    except Exception:
        return {"items": []}
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


def _live_us_model(session: Session, user_id: int) -> dict:
    """미국 모델 신호 — TF(추세 필터 보유) 전략, 라이브 계산 (2026-08-31 시장별 분리).

    모델 자본 $1,000,000(센트). 보유=BULL / 현금 대기=NEUTRAL 로 표기.
    """
    from app.backtests import load_aligned_bars as _load
    from app.strategy.trendfilter import run_tf_backtest

    try:
        bars, _, _ = _load(session, date(1990, 1, 1), date(2100, 1, 1), codes=("QQQ", "QQQ"))
    except Exception as exc:
        return {"status": "MISSING", "reason": str(exc)[:300], "market": "US"}
    result = run_tf_backtest(bars, MODEL_CAPITAL)
    lp = result.plans[-1]
    return {
        "status": lp.status, "trade_date": bars[-1]["date"], "version": 0, "market": "US",
        "strategy": "TF",
        "regime": lp.regime.value if lp.status == "OK" else None,
        "e_target": lp.e_target, "w_200": lp.w_200, "w_lev": 0.0,
        "gap_cancel_below": None,
        "indicators": {k: v for k, v in lp.indicators.items() if v is not None},
        "detail": {
            "model_capital": MODEL_CAPITAL,
            "model_equity": round(result.equity[-1]) if result.equity else MODEL_CAPITAL,
            "model_cash": round(result.cash_curve[-1]) if result.cash_curve else MODEL_CAPITAL,
            "model_qty_200": result.qty_200[-1] if result.qty_200 else 0,
            "model_qty_lev": 0,
        },
        "orders": [
            {"instrument": o.instrument, "side": o.side, "otype": o.otype,
             "qty": o.qty, "price": o.price, "kind": o.kind} for o in lp.orders
        ],
        "basis": "model",
    }


def _tf_portfolio_orders(session: Session, pf_row, pid: int) -> dict:
    """미국 포트 기준 TF 주문표 — 목표는 '전량 보유' 또는 '전량 현금' (2026-08-31).

    QLD/TQQQ 등 전략 외 보유는 항상 청산 대상으로 표기한다.
    """
    from app.backtests import load_aligned_bars as _load
    from app.models import Instrument
    from app.strategy.trendfilter import TF_EXIT_BUFFER, TF_MA, run_tf_backtest

    bars, _, _ = _load(session, date(1990, 1, 1), date(2100, 1, 1), codes=("QQQ", "QQQ"))
    result = run_tf_backtest(bars, MODEL_CAPITAL)
    lp = result.plans[-1]
    want_hold = lp.status == "OK" and lp.regime is Regime.BULL
    base_day = date.fromisoformat(bars[-1]["date"])
    exec_day = _next_exec_day(base_day)

    # 계좌 상태 = 신호 기준일 종가 시점 — B안 (RAVG 쪽과 동일 계약, 2026-09-02)
    lot_rows, cash = _state_before(session, pid, exec_day)
    qty_qqq = qty_lev = 0
    for l in lot_rows:
        code = session.get(Instrument, l["instrument_id"]).code
        if code == "QQQ":
            qty_qqq += l["qty"]
        else:
            qty_lev += l["qty"]

    close = float(bars[-1]["close"])
    orders = []
    if qty_lev > 0:  # 전략 외 자산은 상태 무관 청산
        orders.append({"instrument": "LEV", "side": "sell", "otype": "market",
                       "qty": qty_lev, "price": None, "kind": "tf_exit"})
    if want_hold:
        est = int((cash + (qty_lev * close if qty_lev else 0)) / close) if close else 0
        if est > 0:
            orders.append({"instrument": "K200", "side": "buy", "otype": "market",
                           "qty": est, "price": None, "kind": "tf_entry"})
    elif qty_qqq > 0:
        orders.append({"instrument": "K200", "side": "sell", "otype": "market",
                       "qty": qty_qqq, "price": None, "kind": "tf_exit"})

    equity = round(cash + (qty_qqq + qty_lev) * close)
    out = {
        "basis": "portfolio", "strategy": "TF",
        "portfolio": {"id": pf_row.id, "name": pf_row.name},
        "account": {"cash": cash, "qty_200": qty_qqq, "qty_lev": qty_lev, "equity": equity},
        "orders": orders, "gap_cancel_below": None,
    }
    # 계획 스냅샷 (일자별 일지) — B안 이후 결정론적 upsert
    from app.models import PortfolioPlan
    row = session.scalar(select(PortfolioPlan).where(
        PortfolioPlan.portfolio_id == pid, PortfolioPlan.trade_date == exec_day))
    payload = {"regime": lp.regime.value, "signal_date": base_day.isoformat(),
               "orders": out["orders"], "gap_cancel_below": None,
               "account": out["account"], "e_target": lp.e_target, "strategy": "TF"}
    from app.dashboard import kst_today
    if row is None:
        session.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload=payload))
    elif exec_day > kst_today():
        row.payload = payload  # 아직 실행 전 — 최신 상태로 갱신
    # 실행일 도래(오늘·과거) 계획은 불변 — "그날 아침의 계획" 보존 (2026-09-02 지시)
    session.commit()
    return out


@router.get("/signals/daily")
def get_daily_signal(date_: date | None = Query(default=None, alias="date"),
                     portfolio_id: int | None = None,
                     market: str = "KR",
                     _user: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    if portfolio_id is not None:
        pass  # 포트 기준이면 포트의 market 을 따름 (아래 분기)
    elif market == "US":
        return _live_us_model(session, _user)
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
    if portfolio_id is not None:
        pf_row = session.get(TradePortfolio, portfolio_id)
        if pf_row is not None and pf_row.market == "US":
            # 미국 포트 — TF 전략 기준 (2026-08-31 시장별 분리)
            if pf_row.user_id != _user:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="portfolio not found")
            base = _live_us_model(session, _user)
            if base["status"] != "OK":
                return base
            base.update(_tf_portfolio_orders(session, pf_row, portfolio_id))
            return base
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
