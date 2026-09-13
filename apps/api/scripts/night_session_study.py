"""야간장(NXT) 매매가 유리한가 — 시가 체결 이점의 크기 측정 (2026-09-13 사용자 질문, 11월 대비).

현행 체결 규칙(feature-backtest §5.1)은 지정가 매수에 **두 경로**가 있다:
  · 시가 ≤ 지정가  → **시가**에 체결 (갭 하락분만큼 지정가보다 싸게 산다)
  · 저가 ≤ 지정가  → 지정가에 체결
야간장에서 같은 주문을 내면 가격이 지정가를 **지나가는 순간** 체결되므로 첫 경로의 이점이 사라진다 —
밤새 −3% 갭이 나도 우리 지정가(−1.5%)에서 먼저 체결되고, 다음 날 시가(−3%)는 받지 못한다.
매도(익절)도 대칭이다: 지금은 갭 상승 시 시가에 팔지만, 야간장이면 익절가에 먼저 팔린다.

이 스크립트는 그 이점을 끈 버전(N1)을 돌려 **야간 매매의 상한**을 잰다. 호가 두께·스프레드·NAV 괴리는
더 나쁜 방향으로만 작용하므로, N1 이 현행보다 못하면 야간 매매는 검토할 필요가 없다.

실행: docker compose exec -T api python -m scripts.night_session_study
"""
from __future__ import annotations

import math
import random
from datetime import date
from statistics import mean, median

import app.strategy.backtest as bt
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

CAP, W, H, STEP = 100_000_000.0, 260, 246, 21
_REAL_BUY, _REAL_SELL = bt._fill_limit_buy, bt._fill_limit_sell


def night_buy(limit: int, open_px: float, low: float) -> float | None:
    """야간장: 가격이 지정가를 지나가면 **지정가**에 체결 — 시가 갭 이점 없음."""
    if open_px <= limit or low <= limit:
        return float(limit)
    return None


def night_sell(limit: int, open_px: float, high: float) -> float | None:
    if open_px >= limit or high >= limit:
        return float(limit)
    return None


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


def run_night(b200, blev, params, start, buy=True, sell=True):
    if buy:
        bt._fill_limit_buy = night_buy
    if sell:
        bt._fill_limit_sell = night_sell
    try:
        return run_backtest(b200, blev, CAP, params, start_index=start)
    finally:
        bt._fill_limit_buy, bt._fill_limit_sell = _REAL_BUY, _REAL_SELL


def main() -> None:
    from app.backtests import load_aligned_bars
    from app.db import SessionLocal

    with SessionLocal() as s:
        b200, blev, fp = load_aligned_bars(s, date(2017, 1, 2), date(2026, 12, 31))
    P = Params()
    dates = [b["date"] for b in b200]
    print(f"데이터 {dates[0]}~{dates[-1]} {len(b200)}봉 · 지문 {str(fp)[:16]}")

    base = run_backtest(b200, blev, CAP, P, start_index=W, collect_plans=True)

    # ── 시가 체결이 실제로 얼마나 자주·얼마나 유리한가
    idx = {d: n for n, d in enumerate(dates)}
    gap_fills, touch_fills, adv = 0, 0, []
    for f in base.fills:
        if f.side != "buy" or not (f.kind.startswith("grid") or f.kind == "boot"):
            continue
        i = idx.get(f.date)
        if i is None:
            continue
        o = float(b200[i]["open"])
        if abs(f.price - round(o)) <= 1:           # 시가 체결
            gap_fills += 1
        else:
            touch_fills += 1
    # 계획된 지정가 대비 시가 이점 — 계획일 주문과 다음 봉 시가를 직접 비교
    for n, p in enumerate(base.plans):
        if p.status != "OK":
            continue
        i = idx.get(base.dates[n])
        if i is None:
            continue
        o = float(b200[i]["open"])
        for od in p.orders:
            if od.side == "buy" and od.otype == "limit" and od.price and o <= od.price:
                adv.append(od.price / o - 1)
    print(f"\n[0] 지정가 매수 체결 경로 (현행): 시가 체결 {gap_fills}건 · 지정가 터치 {touch_fills}건"
          f" · 시가 체결 비중 {gap_fills / max(gap_fills + touch_fills, 1):.1%}")
    if adv:
        a = sorted(adv)
        print(f"    시가가 지정가보다 쌌던 주문 {len(adv)}건 · 이점 평균 {mean(adv):.2%} · 중앙 {median(adv):.2%}"
              f" · 90% {a[int(len(a) * 0.9)]:.2%} · 최대 {max(adv):.2%}")

    print("\n[1] 야간장 체결(지정가에 먼저 체결 — 시가 이점 없음)")
    print(f"{'변형':<30}{'누적':>10}{'CAGR':>8}{'MDD':>9}{'샤프':>7}{'체결':>6}{'짝 부트스트랩 90%':>26}")
    print(f"{'현행 (KRX 09:00 체결)':<30}{base.kpi['total_return']:>10.2%}{base.kpi['cagr']:>8.2%}"
          f"{mdd_of(base.equity):>9.2%}{base.kpi['sharpe']:>7.3f}{len(base.fills):>6}{'—':>26}")
    res = {}
    for nm, kw in (("N1 야간 매수·매도", dict(buy=True, sell=True)),
                   ("N2 야간 매수만", dict(buy=True, sell=False)),
                   ("N3 야간 매도만", dict(buy=False, sell=True))):
        r = run_night(b200, blev, P, W, **kw)
        res[nm] = (r, kw)
        bs = paired_block_bootstrap(base.equity, r.equity)
        band = f"{bs['p05']:+.4f}~{bs['p95']:+.4f}({bs['share_pos']:.0%})"
        print(f"{nm:<30}{r.kpi['total_return']:>10.2%}{r.kpi['cagr']:>8.2%}{mdd_of(r.equity):>9.2%}"
              f"{r.kpi['sharpe']:>7.3f}{len(r.fills):>6}{band:>26}")

    print("\n[2] 89개 1년 창 — 정확 차이")
    starts = list(range(W, len(b200) - H, STEP))
    print(f"{'변형':<30}{'평균':>10}{'중앙':>10}{'최악':>10}{'최선':>10}{'이김':>6}{'짐':>5}")
    for nm, (r, kw) in res.items():
        d = []
        for st_ in starts:
            aa = run_backtest(b200[:st_ + H], blev[:st_ + H], CAP, P, start_index=st_)
            bb = run_night(b200[:st_ + H], blev[:st_ + H], P, st_, **kw)
            d.append(bb.equity[-1] / CAP - aa.equity[-1] / CAP)
        print(f"{nm:<30}{mean(d):>+10.3%}{median(d):>+10.3%}{min(d):>+10.3%}{max(d):>+10.3%}"
              f"{sum(1 for x in d if x > 0):>6}{sum(1 for x in d if x < 0):>5}")

    print("\n[3] 야간 슬리피지 민감도 — 시장가 줄(축소·레버리지)에 추가 비용을 물리면")
    import dataclasses
    for extra in (0.001, 0.002, 0.005):
        p2 = dataclasses.replace(P, slippage_market=P.slippage_market + extra)
        r = run_night(b200, blev, p2, W, buy=True, sell=True)
        print(f"  시장가 슬리피지 {P.slippage_market + extra:.2%} (현행 {P.slippage_market:.2%} + {extra:.1%}): "
              f"누적 {r.kpi['total_return']:>8.2%} · MDD {mdd_of(r.equity):>7.2%} · 샤프 {r.kpi['sharpe']:.3f}")


if __name__ == "__main__":
    main()
