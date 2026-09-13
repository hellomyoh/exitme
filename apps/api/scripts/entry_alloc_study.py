"""진입 배분 연구 (r2, 2026-09-13) — 플래너 매수 구간의 **충실한 복제**에 스위치를 달아 변형을 비교한다.

1차(scratchpad/entry_alloc_study.py, docs/entry-allocation-review-20260913.md)의 결함을 반론서가 짚었다:
  · 레버리지 재계산이 `lev_liq` 매도 유무만 보고 **σ20 > 35% 게이트를 확인하지 않아**, 보유가 없는 날 금지된 레버리지 매수를 만들었다
  · 추가한 그리드 단이 기존 레버리지 주문의 현금 점유를 빼지 않아 **공동 예산이 아니었다**
  · 가용 현금에 레버리지 매도대금을 넣고, boot_f 를 역산하고, 수수료 포함 수량에 본금만 차감하는 등 플래너와 달랐다
여기서는 planner.plan() 의 매수 구간(부트스트랩·그리드·레버리지 전략·전술·총상한)을 그대로 옮기고,
  · 스위치 없음 → 플래너 주문과 **매일 완전 일치**해야 한다 (check_equivalence)
  · clip_rung   → 현금이 모자라는 그리드 단을 생략 대신 int(cash // (price×(1+c))) 로 축소
  · lev_first   → 레버리지 전략 매수를 그리드 예약 전에 크기 결정 (게이트·자격·청산 조건은 그대로)
변형마다 수수료 포함 예산 위반 일수, 바뀐 날의 σ20·레짐·현금, 89창 **정확 차이**, 짝 블록 부트스트랩을 낸다.

실행:  docker compose exec -T api python -m scripts.entry_alloc_study            (전 구간 + 89창 + 부트스트랩)
       docker compose exec -T api python -m scripts.entry_alloc_study --quick    (전 구간만)
"""
from __future__ import annotations

import argparse
import math
import random
from dataclasses import replace
from datetime import date
from statistics import mean, median

import app.strategy.backtest as bt
from app.strategy.backtest import run_backtest
from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, LEV, Order, Plan
from app.strategy.regime import Regime

CAP, W, H, STEP = 100_000_000.0, 260, 246, 21
_REAL_PLAN = bt.plan


def rebuild_buys(p: Plan, i, m200, mlev, pf, params: Params, days_since_start,
                 clip_rung: bool = False, lev_first: bool = False) -> tuple[list[Order], list[Order], dict]:
    """플래너 매수 구간 복제. 반환 (매수 주문들, 총상한 매도, 진단).

    매도(축소·익절·강제청산·전략 축소·전술 이탈)는 플래너 것을 그대로 쓴다 — 이 함수는 매수와 lev_cap 만 다시 만든다.
    연구용 부트스트랩 옵션(price_ref·ma_filter·otype·slippage)은 기본값만 지원한다.
    """
    f = params.flags
    if params.boot_price_ref != "close" or params.boot_ma_filter or params.boot_otype != "limit":
        raise ValueError("연구용 부트스트랩 옵션은 복제하지 않는다 — 기본값으로 돌려라")
    regime = p.regime
    close = m200.closes[i]
    sigma20 = m200.sigma20[i]
    lev_close = mlev.closes[i]
    equity = pf.equity(close, lev_close)
    value_200 = pf.value(K200, close)
    target_200 = p.w_200 * equity
    w_lev = p.w_lev
    grid = p.indicators["grid"]
    reduce_qty = sum(o.qty for o in p.orders if o.instrument == K200 and o.kind == "reduce")
    cash_left = pf.cash + reduce_qty * close - equity * params.cash_buffer
    cash0 = cash_left
    force_liq = (regime is not Regime.BULL) or (sigma20 is not None and sigma20 > params.sigma20_liquidate)
    lev_lots = [l for l in pf.lots if l.instrument == LEV]
    if f.f4_leverage and (force_liq or w_lev == 0.0):
        lev_lots = []          # 플래너: 강제청산·E≤1 이면 전량 매도 주문 뒤 빈 목록
    buys: list[Order] = []

    # ── 부트스트랩 (ADR-010) — 플래너와 같은 식
    boot_f = 0.0
    if (days_since_start is not None and 0 <= days_since_start < params.boot_days and params.boot_frac > 0
            and target_200 > value_200):
        boot_f = min(1.0, params.boot_frac * (params.boot_bear_mult if regime is Regime.BEAR else 1.0))
        boot_price = round_tick(close * (1 - params.boot_delta * grid), params.tick, up=False)
        boot_qty = int(boot_f * (target_200 - value_200) // boot_price) if boot_price > 0 else 0
        if boot_qty > 0 and boot_qty * boot_price <= cash_left:
            cash_left -= boot_qty * boot_price
            buys.append(Order(K200, "buy", "limit", boot_qty, boot_price, "boot"))
        else:
            boot_f = 0.0

    def grid_orders():
        nonlocal cash_left
        if regime is Regime.BEAR:
            return
        remaining = max(0.0, target_200 - value_200) if f.f5_gap_filter else target_200
        remaining *= (1.0 - boot_f)
        ws = list(params.grid_weights[:params.grid_steps])
        ws += [0.0] * (params.grid_steps - len(ws))
        total_w = sum(ws)
        if total_w <= 0:
            ws = [1.0 / params.grid_steps] * params.grid_steps
            total_w = 1.0
        for k in range(1, params.grid_steps + 1):
            price = round_tick(close * (1 - grid * k), params.tick, up=False)
            qty = int(remaining * (ws[k - 1] / total_w) // price)
            if qty <= 0:
                continue
            if qty * price > cash_left:
                if not clip_rung:
                    continue
                qty = int(cash_left // (price * (1 + params.commission)))   # 수수료 포함 예산으로 축소
                if qty <= 0:
                    continue
            cash_left -= qty * price
            buys.append(Order(K200, "buy", "limit", qty, price, f"grid{k}"))

    def lev_orders():
        nonlocal cash_left
        if not (f.f4_leverage and regime is Regime.BULL and not force_liq and w_lev > 0):
            return
        lev_scale = 2.0 / params.lev_multiple
        strat_target = w_lev * params.lev_strategic_ratio * equity * lev_scale
        strat_value = sum(l.qty * lev_close for l in lev_lots if l.kind == "lev_strat")
        diff = strat_target - strat_value
        if diff > 0 and (strat_value == 0.0 or diff > params.band * equity):
            qty = int(min(diff, cash_left) / lev_close) if cash_left > 0 else 0
            if qty > 0:
                cash_left -= qty * lev_close
                buys.append(Order(LEV, "buy", "market", qty, None, "lev_strat"))
        lev_ema, lev_atr = mlev.ema20[i], mlev.atr20[i]
        if lev_ema is not None and lev_atr is not None:
            tact_budget_each = w_lev * (1 - params.lev_strategic_ratio) * equity * lev_scale / 2
            has1 = any(l.kind == "lev_tact1" for l in lev_lots)
            has2 = any(l.kind == "lev_tact2" for l in lev_lots)
            if lev_close < lev_ema - params.lev_tact1_mult * lev_atr and not has1:
                qty = int(min(tact_budget_each, cash_left) / lev_close) if cash_left > 0 else 0
                if qty > 0:
                    cash_left -= qty * lev_close
                    buys.append(Order(LEV, "buy", "market", qty, None, "lev_tact1"))
            if lev_close < lev_ema - params.lev_tact2_mult * lev_atr and not has2:
                qty = int(min(tact_budget_each, cash_left) / lev_close) if cash_left > 0 else 0
                if qty > 0:
                    cash_left -= qty * lev_close
                    buys.append(Order(LEV, "buy", "market", qty, None, "lev_tact2"))

    if lev_first:
        lev_orders()
        grid_orders()
    else:
        grid_orders()
        lev_orders()

    # ── 총 레버리지 상한 (ADR-012) — 플래너와 같은 식, 계획된 레버리지 매수·매도 반영
    cap: list[Order] = []
    if f.f4_leverage and regime is Regime.BULL and not force_liq and w_lev > 0 and lev_lots:
        lev_scale_cap = 2.0 / params.lev_multiple
        lev_target_total = w_lev * equity * lev_scale_cap
        held = sum(l.qty for l in lev_lots)
        planned = sum(o.qty for o in buys if o.instrument == LEV) \
            - sum(o.qty for o in p.orders if o.instrument == LEV and o.side == "sell" and o.kind != "lev_cap")
        excess = (held + planned) * lev_close - lev_target_total
        if excess > params.band * equity:
            qty = min(int(excess / lev_close), held + max(planned, 0))
            if qty > 0:
                cap.append(Order(LEV, "sell", "market", qty, None, "lev_cap"))

    fee_budget = sum(o.qty * (o.price if o.price else lev_close) * (1 + params.commission) for o in buys)
    diag = {"cash0": cash0, "cash_left": cash_left, "sigma20": sigma20, "force_liq": force_liq,
            "fee_budget": fee_budget, "cash_avail": pf.cash + reduce_qty * close, "equity": equity}
    return buys, cap, diag


def _key(o: Order):
    return (o.instrument, o.side, o.otype, o.qty, o.price, o.kind)


def _is_buy_or_cap(o: Order) -> bool:
    return o.side == "buy" or o.kind == "lev_cap"


def make_plan(clip_rung=False, lev_first=False, log: list | None = None, base_log: dict | None = None,
              budget_log: list | None = None):
    """bt.plan 대체 — 플래너 계획을 받아 매수·총상한만 복제본으로 바꾼다."""
    def wrapped(i, m200, mlev, prev_regime, pf, params, days_since_start=None):
        p = _REAL_PLAN(i, m200, mlev, prev_regime, pf, params, days_since_start=days_since_start)
        if p.status != "OK":
            return p
        buys, cap, diag = rebuild_buys(p, i, m200, mlev, pf, params, days_since_start, clip_rung, lev_first)
        kept = [o for o in p.orders if not _is_buy_or_cap(o)]
        orders = tuple(kept + buys + cap)
        if budget_log is not None and diag["fee_budget"] > diag["cash_avail"] + 1e-6:
            budget_log.append((i, diag["fee_budget"] - diag["cash_avail"]))
        if log is not None:
            mine = sorted(_key(o) for o in buys + cap)
            theirs = sorted(_key(o) for o in p.orders if _is_buy_or_cap(o))
            if mine != theirs:
                log.append({"i": i, "regime": p.regime.value, "sigma20": diag["sigma20"], "e": p.e_target,
                            "cash0": diag["cash0"], "planner": theirs, "variant": mine})
        return replace(p, orders=orders)
    return wrapped


def run_variant(bars200, barslev, params, start, clip_rung=False, lev_first=False, log=None, budget_log=None):
    bt.plan = make_plan(clip_rung, lev_first, log=log, budget_log=budget_log)
    try:
        return run_backtest(bars200, barslev, CAP, params, start_index=start)
    finally:
        bt.plan = _REAL_PLAN


def check_equivalence(bars200, barslev, params, start=W) -> tuple[int, list]:
    """스위치 없는 복제본이 플래너 주문과 매일 일치하는지. 반환 (검사한 계획일 수, 불일치 목록)."""
    log: list = []
    days = {"n": 0}
    inner = make_plan(False, False, log=log)

    def counting(i, m200, mlev, prev_regime, pf, params_, days_since_start=None):
        p = inner(i, m200, mlev, prev_regime, pf, params_, days_since_start=days_since_start)
        if p.status == "OK":
            days["n"] += 1
        return p
    bt.plan = counting
    try:
        run_backtest(bars200, barslev, CAP, params, start_index=start)
    finally:
        bt.plan = _REAL_PLAN
    return days["n"], log


def mdd_of(eq):
    p, w = eq[0], 0.0
    for v in eq:
        p = max(p, v)
        w = min(w, v / p - 1)
    return w


def daily_logret(eq, cap=CAP):
    prev, out = cap, []
    for v in eq:
        out.append(math.log(v / prev))
        prev = v
    return out


def paired_block_bootstrap(eq_base, eq_var, block=21, reps=2000, seed=20260913):
    """두 자산곡선(같은 날짜)의 일수익 차이를 블록 재표집 — 누적 차이(로그)의 분포. 단일 전략 신뢰구간과 다르다."""
    d = [b - a for a, b in zip(daily_logret(eq_base), daily_logret(eq_var))]
    n = len(d)
    rng = random.Random(seed)
    sums = []
    for _ in range(reps):
        tot, m = 0.0, 0
        while m < n:
            st = rng.randrange(n)
            for k in range(block):
                if m >= n:
                    break
                tot += d[(st + k) % n]
                m += 1
        sums.append(tot)
    sums.sort()
    return {"obs": sum(d), "p05": sums[int(reps * 0.05)], "p50": sums[reps // 2], "p95": sums[int(reps * 0.95)],
            "share_pos": sum(1 for x in sums if x > 0) / reps}


def main(quick: bool = False) -> None:
    from app.backtests import load_aligned_bars
    from app.db import SessionLocal

    with SessionLocal() as s:
        b200, blev, fp = load_aligned_bars(s, date(2017, 1, 2), date(2026, 12, 31))
    P = Params()
    dates = [b["date"] for b in b200]
    print(f"데이터 {dates[0]}~{dates[-1]} {len(b200)}봉 · 지문 {str(fp)[:16]} · 엔진 기본값")

    n, mism = check_equivalence(b200, blev, P)
    print(f"\n[1] 등가성 — 스위치 없는 복제본 vs 플래너: 계획일 {n}개 중 불일치 {len(mism)}개")
    for m in mism[:5]:
        print("   ", dates[m["i"]], m["planner"], "≠", m["variant"])
    if mism:
        raise SystemExit("복제본이 플래너와 다르다 — 변형 비교를 진행하지 않는다")

    base = run_backtest(b200, blev, CAP, P, start_index=W)
    variants = [("A1 생략→축소", dict(clip_rung=True)),
                ("A3 레버리지 우선", dict(lev_first=True)),
                ("A13 레버리지 우선 + 축소", dict(clip_rung=True, lev_first=True))]
    print("\n[2] 전 구간 (2018-01-26~) — 누적·MDD·샤프·체결, 바뀐 계획일, 수수료 포함 예산 위반 일수")
    print(f"{'변형':<24}{'누적':>9}{'CAGR':>8}{'MDD':>9}{'샤프':>7}{'체결':>6}{'바뀐날':>7}{'예산위반':>9}{'그중 σ20>35%':>13}")
    print(f"{'현행':<24}{base.kpi['total_return']:>9.2%}{base.kpi['cagr']:>8.2%}{mdd_of(base.equity):>9.2%}"
          f"{base.kpi['sharpe']:>7.3f}{len(base.fills):>6}{'—':>7}{'—':>9}{'—':>13}")
    results = {}
    for nm, kw in variants:
        log, blog = [], []
        r = run_variant(b200, blev, P, W, log=log, budget_log=blog, **kw)
        hi = sum(1 for m in log if m["sigma20"] is not None and m["sigma20"] > P.sigma20_liquidate)
        results[nm] = (r, kw, log, blog)
        print(f"{nm:<24}{r.kpi['total_return']:>9.2%}{r.kpi['cagr']:>8.2%}{mdd_of(r.equity):>9.2%}"
              f"{r.kpi['sharpe']:>7.3f}{len(r.fills):>6}{len(log):>7}{len(blog):>9}{hi:>13}")
        bs = paired_block_bootstrap(base.equity, r.equity)
        print(f"    누적 차이(로그) 관측 {bs['obs']:+.4f} · 짝 부트스트랩 5%/50%/95% {bs['p05']:+.4f}/{bs['p50']:+.4f}/{bs['p95']:+.4f}"
              f" · 양(+)일 확률 {bs['share_pos']:.0%}")
        for m in log[:6]:
            print(f"    {dates[m['i']]} {m['regime']} σ20 {m['sigma20']:.1%} E {m['e']:.2f} 현금 {m['cash0']:,.0f}"
                  f"  플래너 {[(k[5], k[3]) for k in m['planner']]} → 변형 {[(k[5], k[3]) for k in m['variant']]}")
        if len(log) > 6:
            print(f"    … 외 {len(log) - 6}일")

    if quick:
        return
    print("\n[3] 89개 1년 창 — 정확 차이 (허용오차 없음)")
    starts = list(range(W, len(b200) - H, STEP))
    print(f"{'변형':<24}{'평균':>10}{'중앙':>10}{'최악':>10}{'최선':>10}{'수익≠0':>7}{'이김':>5}{'짐':>4}{'MDD≠0':>7}{'MDD범위(%p)':>22}")
    for nm, (r, kw, _, _) in results.items():
        d, dm = [], []
        for st_ in starts:
            a = run_backtest(b200[:st_ + H], blev[:st_ + H], CAP, P, start_index=st_)
            b = run_variant(b200[:st_ + H], blev[:st_ + H], P, st_, **kw)
            d.append(b.equity[-1] / CAP - a.equity[-1] / CAP)
            dm.append(mdd_of(b.equity) - mdd_of(a.equity))
        print(f"{nm:<24}{mean(d):>+10.4%}{median(d):>+10.4%}{min(d):>+10.4%}{max(d):>+10.4%}"
              f"{sum(1 for x in d if x != 0):>7}{sum(1 for x in d if x > 0):>5}{sum(1 for x in d if x < 0):>4}"
              f"{sum(1 for x in dm if x != 0):>7}{f'{min(dm) * 100:+.4f} ~ {max(dm) * 100:+.4f}':>22}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="진입 배분 연구 r2")
    ap.add_argument("--quick", action="store_true", help="전 구간만 (89창 생략)")
    main(ap.parse_args().quick)
