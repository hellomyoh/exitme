"""야간 정보의 상한 측정 — "시가를 알면 주문표가 나아지는가" (2026-09-13, 애프터마켓 분석서 검증).

애프터마켓 분석서는 구성종목 야간가격으로 **다음 날 시가를 추정**해 주문 가격·수량에 반영하자고 제안한다.
그런데 우리 실행기는 09:01 에 **실제 시가를 이미 읽는다**(ADR-009 §2-3, 갭 취소 판정). 즉 야간 추정치가 줄 수 있는
최선은 "그 시가를 미리 아는 것"이고, **실제 시가를 그대로 써도 개선이 없다면 그 추정치는 그보다 나을 수 없다.**

그래서 야간 자료 없이도 상한을 잴 수 있다. 변형 셋 (모두 09:01 에 합법적으로 관측 가능한 정보만 사용):
  S1 시가 앵커 — 그리드 지정가를 전일 종가 대신 **당일 시가** 기준으로 (`open × (1 − Grid×k)`)
  S2 시가 평가 — 목표·잔여예산을 **시가로 재평가**한 자산으로 (수량만 바뀜, 가격은 종가 기준 유지)
  S3 둘 다
그리고 도달 불가능한 오라클 둘 (상한의 상한):
  O1 완전 예지 — 그날 종가가 시가보다 낮으면 그날 매수 전량 취소
  O2 완전 예지 앵커 — 그날 **저가** 기준으로 지정가를 깐다(체결률 100% 지향)

실행: docker compose exec -T api python -m scripts.open_anchor_study
"""
from __future__ import annotations

import math
import random
from datetime import date
from statistics import mean, median

import app.strategy.backtest as bt
from app.strategy.backtest import run_backtest
from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, Order
from app.strategy.regime import Regime

CAP, W, H, STEP = 100_000_000.0, 260, 246, 21
_REAL_PLAN = bt.plan


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


def paired_block_bootstrap(eq_a, eq_b, block=21, reps=2000, seed=20260913):
    d = [b - a for a, b in zip(daily_logret(eq_a), daily_logret(eq_b))]
    n, rng, sums = len(d), random.Random(seed), []
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
    return {"obs": sum(d), "p05": sums[int(reps * 0.05)], "p50": sums[reps // 2],
            "p95": sums[int(reps * 0.95)], "share_pos": sum(1 for x in sums if x > 0) / reps}


def make_plan(anchor_open=False, size_open=False, oracle_skip=False, oracle_low=False):
    """주문표의 그리드 매수만 바꾼다. 매도·레버리지·부트스트랩·갭 필터는 그대로."""
    def wrapped(i, m200, mlev, prev_regime, pf, params, days_since_start=None):
        p = _REAL_PLAN(i, m200, mlev, prev_regime, pf, params, days_since_start=days_since_start)
        if p.status != "OK" or p.regime is Regime.BEAR or i + 1 >= len(m200.opens):
            return p
        grids = [o for o in p.orders if o.instrument == K200 and o.side == "buy" and o.kind.startswith("grid")]
        if not grids:
            return p
        nxt_open, nxt_close, nxt_low = m200.opens[i + 1], m200.closes[i + 1], m200.lows[i + 1]
        others = [o for o in p.orders if o not in grids]
        if oracle_skip and nxt_close < nxt_open:
            return p.__class__(**{**p.__dict__, "orders": tuple(others)})   # 그날 매수 전량 취소
        close, grid = p.indicators["close"], p.indicators["grid"]
        equity = p.indicators["equity"]
        # 수량 재평가 — 시가로 자산·보유를 다시 재면 잔여예산이 달라진다
        scale = 1.0
        if size_open:
            held = sum(l.qty for l in pf.lots if l.instrument == K200)
            eq_open = pf.cash + held * nxt_open + sum(l.qty * mlev.opens[i + 1] for l in pf.lots if l.instrument != K200)
            rem_close = max(0.0, p.w_200 * equity - held * close)
            rem_open = max(0.0, p.w_200 * eq_open - held * nxt_open)
            scale = (rem_open / rem_close) if rem_close > 0 else 0.0
        out = list(others)
        for o in grids:
            k = int(o.kind[4:])
            if oracle_low:
                price = round_tick(nxt_low, params.tick, up=True)             # 그날 저가 — 오라클
            elif anchor_open:
                price = round_tick(nxt_open * (1 - grid * k), params.tick, up=False)
            else:
                price = o.price
            budget = o.qty * o.price * scale
            qty = int(budget // price) if price > 0 else 0
            if qty > 0:
                out.append(Order(K200, "buy", "limit", qty, price, o.kind))
        return p.__class__(**{**p.__dict__, "orders": tuple(out)})
    return wrapped


def run_variant(b200, blev, params, start, **kw):
    bt.plan = make_plan(**kw) if kw else _REAL_PLAN
    try:
        return run_backtest(b200, blev, CAP, params, start_index=start)
    finally:
        bt.plan = _REAL_PLAN


def main() -> None:
    from app.backtests import load_aligned_bars
    from app.db import SessionLocal

    with SessionLocal() as s:
        b200, blev, fp = load_aligned_bars(s, date(2017, 1, 2), date(2026, 12, 31))
    P = Params()
    dates = [b["date"] for b in b200]
    print(f"데이터 {dates[0]}~{dates[-1]} {len(b200)}봉 · 지문 {str(fp)[:16]}")

    # 밤갭 분포 — 야간 신호가 맞혀야 할 대상의 크기
    gaps = [b200[i + 1]["open"] / b200[i]["close"] - 1 for i in range(len(b200) - 1)]
    a = sorted(abs(g) for g in gaps)
    print(f"\n[0] 밤갭(다음 시가/전일 종가 − 1) {len(gaps)}일: 평균 {mean(gaps):+.3%} · 중앙 {median(gaps):+.3%}"
          f" · |갭| 중앙 {a[len(a)//2]:.3%} · |갭| 90% {a[int(len(a)*0.9)]:.3%} · 1% 초과 {sum(1 for g in gaps if abs(g) > 0.01)/len(gaps):.1%}")

    base = run_backtest(b200, blev, CAP, P, start_index=W)
    print("\n[1] 전 구간 — 09:01 에 관측 가능한 정보만 (S) vs 도달 불가 오라클 (O)")
    print(f"{'변형':<28}{'누적':>10}{'CAGR':>8}{'MDD':>9}{'샤프':>7}{'체결':>6}{'짝 부트스트랩 90%':>26}")
    print(f"{'현행 (전일 종가 앵커)':<28}{base.kpi['total_return']:>10.2%}{base.kpi['cagr']:>8.2%}"
          f"{mdd_of(base.equity):>9.2%}{base.kpi['sharpe']:>7.3f}{len(base.fills):>6}{'—':>26}")
    variants = [("S1 시가 앵커", dict(anchor_open=True)),
                ("S2 시가로 수량 재평가", dict(size_open=True)),
                ("S3 시가 앵커+수량", dict(anchor_open=True, size_open=True)),
                ("O1 오라클: 음봉이면 취소", dict(oracle_skip=True)),
                ("O2 오라클: 당일 저가 지정", dict(oracle_low=True))]
    res = {}
    for nm, kw in variants:
        r = run_variant(b200, blev, P, W, **kw)
        res[nm] = (r, kw)
        bs = paired_block_bootstrap(base.equity, r.equity)
        band = f"{bs['p05']:+.4f}~{bs['p95']:+.4f}({bs['share_pos']:.0%})"
        print(f"{nm:<28}{r.kpi['total_return']:>10.2%}{r.kpi['cagr']:>8.2%}{mdd_of(r.equity):>9.2%}"
              f"{r.kpi['sharpe']:>7.3f}{len(r.fills):>6}{band:>26}")

    print("\n[2] 89개 1년 창 — 정확 차이")
    starts = list(range(W, len(b200) - H, STEP))
    print(f"{'변형':<28}{'평균':>10}{'중앙':>10}{'최악':>10}{'최선':>10}{'이김':>6}{'짐':>5}")
    for nm, (r, kw) in res.items():
        d = []
        for st_ in starts:
            aa = run_backtest(b200[:st_ + H], blev[:st_ + H], CAP, P, start_index=st_)
            bb = run_variant(b200[:st_ + H], blev[:st_ + H], P, st_, **kw)
            d.append(bb.equity[-1] / CAP - aa.equity[-1] / CAP)
        print(f"{nm:<28}{mean(d):>+10.3%}{median(d):>+10.3%}{min(d):>+10.3%}{max(d):>+10.3%}"
              f"{sum(1 for x in d if x > 0):>6}{sum(1 for x in d if x < 0):>5}")


if __name__ == "__main__":
    main()
