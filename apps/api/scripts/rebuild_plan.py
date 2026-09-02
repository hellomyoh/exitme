# -*- coding: utf-8 -*-
"""과거 주문표(계획 스냅샷) 재생성 — 동결 도입(2026-09-02) 이전에 덮인 행 복구용.

계획은 B안 규약상 '신호 기준일 종가 상태'의 결정론적 함수이므로,
바를 실행일 전날까지 자르고 원장을 시점 재생(_state_before)하면
그날 아침의 계획이 그대로 재현된다. 기본은 dry-run(비교 출력)이며
--apply 를 줘야 실제로 덮어쓴다. KR(RAVG) 포트 전용.

사용:  python scripts/rebuild_plan.py --portfolio 40 --date 2026-09-02 [--apply]
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from sqlalchemy import select

from app.backtests import base_costs_for, load_aligned_bars, user_algo_overrides
from app.db import SessionLocal
from app.models import Instrument, PortfolioPlan, PositionLot, TradePortfolio
from app.signals import MODEL_CAPITAL, _next_exec_day, _state_before
from app.strategy.backtest import run_backtest
from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, LEV, Lot, Portfolio, grid_ratio, plan, prepare
from app.strategy.regime import Regime


def rebuild(session, pid: int, exec_day: date) -> dict:
    pf = session.get(TradePortfolio, pid)
    if pf is None:
        raise SystemExit(f"포트 {pid} 없음")
    if pf.market == "US":
        raise SystemExit("US(TF) 포트는 지원하지 않음 — KR(RAVG) 전용")

    held = {
        session.get(Instrument, l.instrument_id).code
        for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all()
    }
    pref = (pf.params or {}).get("code_200") if pf.params else None
    if "102110" in held and "069500" not in held:
        code_200 = "102110"
    elif "069500" in held:
        code_200 = "069500"
    else:
        code_200 = pref or "069500"
    etf, codes = ("TIGER" if code_200 == "102110" else "KODEX"), (code_200, "122630")

    algo = user_algo_overrides(session, pf.user_id)
    params = Params(**{**base_costs_for(etf), **algo})
    # 핵심: 바를 실행일 전날까지로 잘라 '그날 아침' 시점을 재현
    bars_200, bars_lev, _ = load_aligned_bars(
        session, date(1990, 1, 1), exec_day - timedelta(days=1), codes=codes)
    base_day = date.fromisoformat(bars_200[-1]["date"])
    if _next_exec_day(base_day) != exec_day:
        print(f"경고: 신호일 {base_day} 의 다음 거래일 != {exec_day} — 실행일이 거래일이 아닐 수 있음")

    result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, params)
    regime = Regime(result.regimes[-1])

    def to_market(bars):
        return prepare([float(b["open"]) for b in bars], [float(b["high"]) for b in bars],
                       [float(b["low"]) for b in bars], [float(b["close"]) for b in bars], params)

    m200, mlev = to_market(bars_200), to_market(bars_lev)
    last = len(bars_200) - 1
    grid_today = grid_ratio(m200.atr20[last], m200.closes[last], params)

    lot_rows, cash = _state_before(session, pid, exec_day)
    lots: list[Lot] = []
    qty_200 = qty_lev = 0
    for l in lot_rows:
        code = session.get(Instrument, l["instrument_id"]).code
        if code == "122630":
            lots.append(Lot(LEV, l["qty"], l["price"], "lev_strat", None, 0))
            qty_lev += l["qty"]
        else:
            if regime is Regime.BULL and params.flags.f1_no_tp_in_bull:
                lots.append(Lot(K200, l["qty"], l["price"], "core", None, 0))
            else:
                tp = round_tick(m200.closes[last] * (1 + grid_today), params.tick, up=True)
                lots.append(Lot(K200, l["qty"], l["price"], "grid", tp, 0))
            qty_200 += l["qty"]

    user_pf = Portfolio(cash=float(cash), lots=lots)
    p = plan(last, m200, mlev, regime, user_pf, params)
    merged: dict[tuple, dict] = {}
    for o in p.orders:
        key = (o.instrument, o.side, o.otype, o.price, o.kind)
        if key in merged:
            merged[key]["qty"] += o.qty
        else:
            merged[key] = {"instrument": o.instrument, "side": o.side, "otype": o.otype,
                           "qty": o.qty, "price": o.price, "kind": o.kind}
    return {"regime": regime.value, "signal_date": base_day.isoformat(),
            "orders": list(merged.values()), "gap_cancel_below": p.gap_cancel_below,
            "account": {"cash": cash, "qty_200": qty_200, "qty_lev": qty_lev,
                        "equity": round(user_pf.equity(m200.closes[last], mlev.closes[last]))},
            "e_target": p.e_target}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", type=int, required=True)
    ap.add_argument("--date", type=date.fromisoformat, required=True, help="실행일 (계획 키)")
    ap.add_argument("--apply", action="store_true", help="실제 덮어쓰기 (기본: 비교만)")
    a = ap.parse_args()

    with SessionLocal() as session:
        payload = rebuild(session, a.portfolio, a.date)
        row = session.scalar(select(PortfolioPlan).where(
            PortfolioPlan.portfolio_id == a.portfolio, PortfolioPlan.trade_date == a.date))
        print("── 재계산 계획:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if row is None:
            print("── 저장된 행 없음 (신규 저장 대상)")
        else:
            same = row.payload == payload
            print(f"── 저장본과 {'일치 — 조치 불필요' if same else '불일치 (덮인 행)'}")
            if not same:
                print(json.dumps(row.payload, ensure_ascii=False, indent=2))
        if a.apply:
            if row is None:
                session.add(PortfolioPlan(portfolio_id=a.portfolio, trade_date=a.date, payload=payload))
            else:
                row.payload = payload
            session.commit()
            print("── 적용 완료")


if __name__ == "__main__":
    main()
