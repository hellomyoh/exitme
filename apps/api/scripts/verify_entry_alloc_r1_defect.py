"""1차 진입 배분 연구(scratchpad)의 결함 재현 — A3 에서 바뀐 날의 σ20 이 모두 청산선을 넘었는지 (2026-09-13 반론 검증).

1차 래퍼의 `lev_plan()` 은 `lev_liq` 매도 주문 유무만 보고 σ20 게이트를 확인하지 않았다. 보유가 없으면 그 매도 주문이
생기지 않으므로 게이트가 통과되고, 플래너가 0주를 내는 날에 레버리지 매수가 만들어진다. 이 스크립트가 그 4일을 찍는다.
사용: docker compose exec -T api python -m scripts.verify_entry_alloc_r1_defect
"""
from datetime import date

import app.strategy.backtest as bt
import app.strategy.planner as pl
from app.backtests import load_aligned_bars
from app.db import SessionLocal
from app.strategy.backtest import run_backtest
from app.strategy.params import Params
from app.strategy.planner import LEV, Order, prepare
from app.strategy.regime import Regime
import dataclasses

CAP, W = 100_000_000.0, 260
P = Params()
with SessionLocal() as s:
    b200, blev, _ = load_aligned_bars(s, date(2017, 1, 2), date(2026, 9, 13))
dates = [b["date"] for b in b200]
m200 = prepare([float(b["open"]) for b in b200], [float(b["high"]) for b in b200],
               [float(b["low"]) for b in b200], [float(b["close"]) for b in b200], P)
real_plan = bt.plan
hits = []


def old_lev_plan(p, pf, params, cash_avail):
    """1차 스크립트의 lev_plan — σ20 게이트 없음(lev_liq 주문 유무만 확인)."""
    if p.regime is not Regime.BULL or p.w_lev <= 0:
        return None
    if any(o.instrument == LEV and o.kind == "lev_liq" for o in p.orders):
        return None
    lev_close, equity = p.indicators["lev_close"], p.indicators["equity"]
    scale = 2.0 / params.lev_multiple
    strat_target = p.w_lev * params.lev_strategic_ratio * equity * scale
    strat_value = sum(l.qty * lev_close for l in pf.lots if l.instrument == LEV and l.kind == "lev_strat")
    diff = strat_target - strat_value
    if diff > 0 and (strat_value == 0.0 or diff > params.band * equity):
        qty = int(min(diff, cash_avail) / lev_close) if cash_avail > 0 else 0
        return (qty, qty * lev_close) if qty > 0 else (0, 0.0)
    return None


def wrapped(i, m200_, mlev, prev_regime, pf, params, days_since_start=None):
    p = real_plan(i, m200_, mlev, prev_regime, pf, params, days_since_start=days_since_start)
    if p.status != "OK" or p.regime is not Regime.BULL:
        return p
    close, grid, equity = p.indicators["close"], p.indicators["grid"], p.indicators["equity"]
    lev_close = p.indicators["lev_close"]
    sell_val = sum(o.qty * (lev_close if o.instrument == LEV else close)
                   for o in p.orders if o.side == "sell" and o.otype == "market")
    boot_cost = sum(o.qty * (o.price or close) for o in p.orders if o.kind == "boot")
    cash0 = pf.cash + sell_val - equity * params.cash_buffer - boot_cost
    lp = old_lev_plan(p, pf, params, cash0)
    if lp is None:
        return p
    planned = sum(o.qty for o in p.orders if o.instrument == LEV and o.kind == "lev_strat" and o.side == "buy")
    out = [o for o in p.orders if not (o.instrument == LEV and o.kind == "lev_strat" and o.side == "buy")]
    if lp[0] != planned:
        s20 = m200_.sigma20[i]
        hits.append((dates[i], s20, planned, lp[0], p.e_target,
                     sum(1 for o in p.orders if o.instrument == LEV and o.kind == "lev_liq")))
    if lp[0] > 0:
        out.append(Order(LEV, "buy", "market", lp[0], None, "lev_strat"))
    return dataclasses.replace(p, orders=tuple(out))


bt.plan = wrapped
try:
    r = run_backtest(b200, blev, CAP, P, start_index=W)
finally:
    bt.plan = real_plan

print(f"1차 A3 에서 레버리지 전략 수량이 달라진 날 {len(hits)}건 — 누적 {r.kpi['total_return']:.2%}")
print(f"{'일자':<12}{'σ20':>8}{'게이트':>8}{'플래너 수량':>11}{'1차 수량':>10}{'E':>7}{'lev_liq 주문':>12}")
for d, s20, was, now, e, liq in hits:
    print(f"{d:<12}{(s20 or 0):>8.2%}{'초과' if s20 and s20 > P.sigma20_liquidate else '정상':>8}"
          f"{was:>11}{now:>10}{e:>7.2f}{liq:>12}")
print(f"\nσ20 > {P.sigma20_liquidate:.0%} 인 날: {sum(1 for _, s, _, _, _, _ in hits if s and s > P.sigma20_liquidate)}/{len(hits)}")
