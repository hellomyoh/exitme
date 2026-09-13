"""보유분으로 시작한 포트의 익절을 **사다리로 나눌 것인가** (2026-09-13 사용자 질문).

현행: '보유분 입력'으로 시작하면 로트가 하나(또는 종목당 하나)라 중립장 익절이 **한 가격·전량**으로 나간다
(`planner.plan` 의 core 로트 익절 = 종가×(1+Grid) 한 줄). 2026-09-03 검토는 "평단 역계산으로 로트를 쪼개는 것"을
기각했는데, 이번 질문은 다르다 — **평단을 건드리지 말고 수량만 사다리로 나누자**는 것이다.

여기서는 core 로트(= 시작 보유분)의 익절 주문만 바꿔 비교한다. 그리드 매수·축소·레버리지·레짐은 전부 불변.
  L0 현행      : 종가×(1+Grid) 한 가격에 전량
  L1 사다리 3단: 50/30/20 을 1×/2×/3×Grid 위에
  L2 균등 3단  : 1/3 씩 1×/2×/3×Grid
  L3 촘촘 3단  : 50/30/20 을 1×/1.5×/2×Grid
  L4 절반만    : 50% 를 1×Grid, 나머지는 보유 (부분 익절)

시작일 87개를 21일 간격으로 쓸어 각 1년을 돌린다. 시작 상태: 현금 40% + K200 60%(시작일 종가 취득).

실행: docker compose exec -T api python -m scripts.entry_holding_tp_study
"""
from __future__ import annotations

import math
import random
from collections import Counter
from datetime import date
from statistics import mean, median

import app.strategy.backtest as bt
from app.strategy.backtest import run_backtest
from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, Order
from app.strategy.regime import Regime

CAP, W, H, STEP = 100_000_000.0, 260, 246, 21
HOLD_FRAC = 0.60          # main() 에서 --hold 로 덮어쓴다
_REAL_PLAN = bt.plan


def mdd_of(eq):
    p, w = eq[0], 0.0
    for v in eq:
        p = max(p, v)
        w = min(w, v / p - 1)
    return w


def make_plan(mults=None, weights=None, start_index=None):
    """core 로트(시작 보유분)의 익절을 사다리로 교체. mults=None 이면 현행 그대로."""
    def wrapped(i, m200, mlev, prev_regime, pf, params, days_since_start=None):
        p = _REAL_PLAN(i, m200, mlev, prev_regime, pf, params, days_since_start=days_since_start)
        if p.status != "OK" or mults is None:
            return p
        core_tp = [o for o in p.orders
                   if o.instrument == K200 and o.side == "sell" and o.kind == "tp" and o.lot_id is not None
                   and pf.lots[o.lot_id].kind == "core"]
        if not core_tp:
            return p
        close, grid = p.indicators["close"], p.indicators["grid"]
        out = [o for o in p.orders if o not in core_tp]
        for o in core_tp:
            total = o.qty
            ws = list(weights)
            tot = sum(ws) or 1.0
            left = total
            for n, (mu, w) in enumerate(zip(mults, ws)):
                q = int(total * w / tot) if n < len(mults) - 1 else left
                q = min(q, left)
                if q <= 0:
                    continue
                left -= q
                price = round_tick(close * (1 + grid * mu), params.tick, up=True)
                out.append(Order(K200, "sell", "limit", q, price, "tp", lot_id=o.lot_id))
        return p.__class__(**{**p.__dict__, "orders": tuple(out)})
    return wrapped


def run_case(b200, blev, params, start, hold_qty, hold_px, mults=None, weights=None):
    bt.plan = make_plan(mults, weights, start)
    try:
        return run_backtest(b200, blev, CAP * (1 - HOLD_FRAC), params, start_index=start,
                            initial_lots=[{"leg": "K200", "qty": hold_qty, "price": hold_px}])
    finally:
        bt.plan = _REAL_PLAN


def tp_stats(r):
    """익절 체결일의 '직전 보유 대비 매도 비율' 분포 — 2026-09-03 검토와 같은 지표."""
    days: Counter = Counter()
    for f in r.fills:
        if f.side == "sell" and f.kind == "tp":
            days[f.date] += f.qty
    if not days:
        return 0, None, None
    ratios = []
    idx = {d: n for n, d in enumerate(r.dates)}
    for d, q in days.items():
        n = idx.get(d)
        if n is None or n == 0:
            continue
        prev = r.qty_200[n - 1]
        if prev > 0:
            ratios.append(min(q / prev, 1.0))
    if not ratios:
        return len(days), None, None
    return len(days), median(ratios), sum(1 for x in ratios if x >= 0.99) / len(ratios)


def main() -> None:
    global HOLD_FRAC
    from app.backtests import load_aligned_bars
    from app.db import SessionLocal

    with SessionLocal() as s:
        b200, blev, fp = load_aligned_bars(s, date(2017, 1, 2), date(2026, 12, 31))
    P = Params()
    dates = [b["date"] for b in b200]
    closes = [float(b["close"]) for b in b200]
    print(f"데이터 {dates[0]}~{dates[-1]} {len(b200)}봉 · 지문 {str(fp)[:16]}")
    print(f"시작 상태: 현금 {1 - HOLD_FRAC:.0%} + K200 {HOLD_FRAC:.0%}(시작일 종가 취득) · 1년 보유 · 시작일 21일 간격")

    cases = [("L0 현행 (한 가격 전량)", None, None),
             ("L1 사다리 50/30/20 · 1·2·3×", (1, 2, 3), (0.5, 0.3, 0.2)),
             ("L2 균등 1/3 · 1·2·3×", (1, 2, 3), (1, 1, 1)),
             ("L3 촘촘 50/30/20 · 1·1.5·2×", (1, 1.5, 2), (0.5, 0.3, 0.2)),
             ("L4 절반만 익절 (1×)", (1, 99), (0.5, 0.5))]
    starts = list(range(W, len(b200) - H, STEP))
    print(f"\n[1] 시작일 {len(starts)}개 × 1년 — 최종 수익률 분포와 익절 행동")
    print(f"{'변형':<28}{'평균':>9}{'중앙':>9}{'최악':>9}{'최선':>9}{'MDD 평균':>10}{'익절일':>7}{'전량 비중':>10}")
    out = {}
    for nm, mults, ws in cases:
        rets, mdds, tpd, full = [], [], [], []
        for st_ in starts:
            hold_px = closes[st_]
            hold_qty = int(CAP * HOLD_FRAC // hold_px)
            r = run_case(b200[:st_ + H], blev[:st_ + H], P, st_, hold_qty, hold_px, mults, ws)
            base = CAP * (1 - HOLD_FRAC) + hold_qty * hold_px
            rets.append(r.equity[-1] / base - 1)
            mdds.append(mdd_of(r.equity))
            n, med, fr = tp_stats(r)
            tpd.append(n)
            if fr is not None:
                full.append(fr)
        out[nm] = rets
        print(f"{nm:<28}{mean(rets):>+9.2%}{median(rets):>+9.2%}{min(rets):>+9.2%}{max(rets):>+9.2%}"
              f"{mean(mdds):>10.2%}{mean(tpd):>7.1f}{(mean(full) if full else 0):>10.0%}")

    print(f"\n[2] 현행 대비 짝 비교 (같은 시작일)")
    print(f"{'변형':<28}{'평균 차이':>10}{'중앙':>10}{'최악':>10}{'최선':>10}{'이김':>6}{'짐':>5}{'동일':>6}")
    b0 = out["L0 현행 (한 가격 전량)"]
    for nm, rets in out.items():
        if nm.startswith("L0"):
            continue
        d = [x - y for x, y in zip(rets, b0)]
        print(f"{nm:<28}{mean(d):>+10.3%}{median(d):>+10.3%}{min(d):>+10.3%}{max(d):>+10.3%}"
              f"{sum(1 for x in d if x > 1e-9):>6}{sum(1 for x in d if x < -1e-9):>5}{sum(1 for x in d if abs(x) <= 1e-9):>6}")

    print("\n[3] 왜 그런가 — 익절가가 한 번에 닿는 빈도 (현행 모델, 전 구간)")
    r = run_backtest(b200, blev, CAP, P, start_index=W)
    n, med, fr = tp_stats(r)
    print(f"  로트가 여러 개인 정본 모델: 익절일 {n}일 · 매도 비율 중앙 {med:.0%} · 전량(≥99%) 비중 {fr:.0%}"
          f"  ← 2026-09-03 검토와 같은 지표")
    # 하루 고가가 1×/2×/3×Grid 를 각각 넘는 빈도
    from app.strategy.planner import prepare
    m = prepare([float(b["open"]) for b in b200], [float(b["high"]) for b in b200],
                [float(b["low"]) for b in b200], closes, P)
    hit = {1: 0, 2: 0, 3: 0}
    n_days = 0
    for i in range(W, len(b200) - 1):
        if m.atr20[i] is None:
            continue
        g = min(max(P.grid_coef * m.atr20[i] / closes[i], P.grid_min), P.grid_max)
        hi = float(b200[i + 1]["high"])
        n_days += 1
        for k in (1, 2, 3):
            if hi >= closes[i] * (1 + g * k):
                hit[k] += 1
    print(f"  다음날 고가가 종가×(1+k·Grid) 도달: 1× {hit[1] / n_days:.1%} · 2× {hit[2] / n_days:.1%} · 3× {hit[3] / n_days:.1%}"
          f"  (표본 {n_days}일)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="보유분 시작 포트의 익절 사다리 검토")
    ap.add_argument("--hold", type=float, default=0.60, help="시작 시 K200 보유 비중 (나머지는 현금)")
    a = ap.parse_args()
    globals()["HOLD_FRAC"] = a.hold
    main()
