"""충격 조기 반응의 가치 — "애프터마켓에 전쟁 같은 충격이 터지면 하루 먼저 줄이는 게 나은가" (2026-09-13 사용자 질문).

현행 타이밍(충격이 T일 저녁에 발생했다고 하자):
  T+1 09:01  동결된 주문표(= T일 종가 기준)로 발주. **갭 필터가 매수는 취소**하지만 매도 계획은 없다 → 그대로 보유
  T+1 저녁   새 종가로 σ·E·레짐 재계산 → 축소·청산 주문 생성
  T+2 09:01  시장가로 축소·청산 **실행**
즉 위험 축소는 충격 **두 장(session) 뒤**에 일어난다. 애프터마켓 정보가 줄 수 있는 최선은 이를 **한 장 앞당기는 것**
(T+1 시가에 매도)이다. 그 이상은 불가능하다 — 우리 종목은 애프터마켓에서 거래되지 않으므로 T일 밤에는 팔 수 없다.

이 스크립트는 그 상한을 잰다. 갭이 `mult × ATR` 을 넘는 날에만, **T+1 시가를 새 종가로 간주해** σ·E·레짐을 다시 계산하고
초과분을 T+1 시가 시장가로 판다(백테스트 시장가 = 다음 봉 시가, 슬리피지 포함). 야간 자료는 필요 없다 — 시가는
09:01 에 우리가 실제로 보는 값이고, 애프터마켓 추정치는 그보다 나을 수 없다.

실행: docker compose exec -T api python -m scripts.shock_reaction_study
"""
from __future__ import annotations

import math
import random
from datetime import date
from statistics import mean, median

import app.strategy.backtest as bt
import app.strategy.indicators as ind
from app.strategy.backtest import run_backtest
from app.strategy.params import Params
from app.strategy.planner import K200, LEV, Order
from app.strategy.regime import Regime, next_regime

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


def _shock_state(m200, i, nxt_open, params):
    """T+1 시가를 '새 종가'로 붙였을 때의 (레짐 입력, E). 지표는 해당 구간만 다시 계산한다."""
    closes = list(m200.closes[:i + 1]) + [float(nxt_open)]
    n = len(closes) - 1
    ma20 = sum(closes[n - 19:n + 1]) / 20
    ma60 = sum(closes[n - 59:n + 1]) / 60
    ma200 = sum(closes[n - 199:n + 1]) / 200
    sd = ind.rolling_vol_annualized(closes[-300:], 20, downside_only=True)[-1]
    s20 = ind.rolling_vol_annualized(closes[-300:], 20)[-1]
    if sd is None or s20 is None:
        return None
    sd = max(sd, params.sigma_down_floor)
    sref = max(m200.sigma_ref[i], params.sigma_down_floor)      # 250일 중앙값 — 하루로 거의 안 변한다
    e_raw = params.blend_abs * (params.target_downside_vol / sd) + (1 - params.blend_abs) * (sref / sd)
    return {"close": closes[-1], "ma20": ma20, "ma60": ma60, "ma200": ma200,
            "e_raw": e_raw, "sigma20": s20}


def make_plan(mult: float, log: list | None = None):
    """갭이 mult×ATR 을 넘는 날, T+1 시가 기준으로 재계산한 목표까지 **시장가 축소**를 미리 낸다."""
    def wrapped(i, m200, mlev, prev_regime, pf, params, days_since_start=None):
        p = _REAL_PLAN(i, m200, mlev, prev_regime, pf, params, days_since_start=days_since_start)
        if p.status != "OK" or i + 1 >= len(m200.opens):
            return p
        close, atr = m200.closes[i], m200.atr20[i]
        nxt_open = m200.opens[i + 1]
        if atr is None or nxt_open > close - mult * atr:
            return p
        st = _shock_state(m200, i, nxt_open, params)
        if st is None:
            return p
        reg = next_regime(p.regime, st["close"], st["ma20"], st["ma60"], st["ma200"], params)
        emax = {Regime.BULL: params.emax_bull, Regime.NEUTRAL: params.emax_neutral,
                Regime.BEAR: params.emax_bear}[reg]
        e = min(emax, st["e_raw"])
        w200 = min(e, 2.0 - e)
        w_lev = max(0.0, e - 1.0)
        lev_open = mlev.opens[i + 1]
        q200 = sum(l.qty for l in pf.lots if l.instrument == K200)
        qlev = sum(l.qty for l in pf.lots if l.instrument == LEV)
        eq_open = pf.cash + q200 * nxt_open + qlev * lev_open
        orders = list(p.orders)
        # 매수는 갭 필터가 이미 지운다(체결 단계). 여기서는 **매도만** 추가한다.
        sell200 = 0
        excess = q200 * nxt_open - w200 * eq_open
        if excess > params.band * eq_open:
            sell200 = min(q200, int(excess / nxt_open))
        force_liq = (reg is not Regime.BULL) or (st["sigma20"] > params.sigma20_liquidate) or w_lev == 0.0
        selllev = qlev if (force_liq and qlev > 0) else 0
        if selllev > 0 and not any(o.instrument == LEV and o.side == "sell" for o in orders):
            orders.append(Order(LEV, "sell", "market", selllev, None, "lev_liq"))
        if sell200 > 0:
            already = sum(o.qty for o in orders if o.instrument == K200 and o.side == "sell" and o.otype == "market")
            add = max(0, sell200 - already)
            if add > 0:
                orders.append(Order(K200, "sell", "market", add, None, "reduce"))
        if log is not None and (sell200 or selllev):
            log.append({"i": i, "gap": nxt_open / close - 1, "regime": p.regime.value, "new_regime": reg.value,
                        "e": p.e_target, "new_e": e, "sell200": sell200, "selllev": selllev})
        return p.__class__(**{**p.__dict__, "orders": tuple(orders)})
    return wrapped


def run_variant(b200, blev, params, start, mult=None, log=None):
    bt.plan = make_plan(mult, log) if mult is not None else _REAL_PLAN
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
    closes = [float(b["close"]) for b in b200]
    opens = [float(b["open"]) for b in b200]
    print(f"데이터 {dates[0]}~{dates[-1]} {len(b200)}봉 · 지문 {str(fp)[:16]}")

    from app.strategy.planner import prepare
    m = prepare(opens, [float(b["high"]) for b in b200], [float(b["low"]) for b in b200], closes, P)
    print("\n[0] 충격 갭(다음 시가 ≤ 종가 − k×ATR) 빈도와 그 다음 5일")
    print(f"{'k':>5}{'일수':>6}{'비율':>8}{'평균 갭':>10}{'최악 갭':>10}{'그날 종가':>10}{'5일 뒤':>9}")
    for k in (1.0, 1.5, 2.0, 3.0):
        days = [i for i in range(W, len(b200) - 6)
                if m.atr20[i] is not None and opens[i + 1] <= closes[i] - k * m.atr20[i]]
        if not days:
            continue
        gaps = [opens[i + 1] / closes[i] - 1 for i in days]
        d1 = [closes[i + 1] / opens[i + 1] - 1 for i in days]
        d5 = [closes[i + 5] / opens[i + 1] - 1 for i in days]
        print(f"{k:>5.1f}{len(days):>6}{len(days) / (len(b200) - W):>8.1%}{mean(gaps):>10.2%}{min(gaps):>10.2%}"
              f"{mean(d1):>10.2%}{mean(d5):>9.2%}")

    base = run_backtest(b200, blev, CAP, P, start_index=W)
    print("\n[1] 충격일에 T+1 시가로 조기 축소·청산 (현행은 T+2 시가)")
    print(f"{'변형':<26}{'누적':>10}{'CAGR':>8}{'MDD':>9}{'샤프':>7}{'발동':>5}{'짝 부트스트랩 90%':>26}")
    print(f"{'현행 (T+2 반응)':<26}{base.kpi['total_return']:>10.2%}{base.kpi['cagr']:>8.2%}"
          f"{mdd_of(base.equity):>9.2%}{base.kpi['sharpe']:>7.3f}{'—':>5}{'—':>26}")
    res = {}
    for k in (1.0, 1.5, 2.0, 3.0):
        log: list = []
        r = run_variant(b200, blev, P, W, mult=k, log=log)
        res[k] = (r, log)
        bs = paired_block_bootstrap(base.equity, r.equity)
        band = f"{bs['p05']:+.4f}~{bs['p95']:+.4f}({bs['share_pos']:.0%})"
        print(f"{f'조기 반응 k={k}':<26}{r.kpi['total_return']:>10.2%}{r.kpi['cagr']:>8.2%}{mdd_of(r.equity):>9.2%}"
              f"{r.kpi['sharpe']:>7.3f}{len(log):>5}{band:>26}")

    print("\n[2] 발동 사례 (k=1.5) — 무엇을 얼마나 팔았나")
    for e in res[1.5][1][:12]:
        print(f"  {dates[e['i'] + 1]} 갭 {e['gap']:+.2%} · {e['regime']}→{e['new_regime']} · E {e['e']:.2f}→{e['new_e']:.2f}"
              f" · K200 {e['sell200']:,}주 · 레버 {e['selllev']:,}주")
    if len(res[1.5][1]) > 12:
        print(f"  … 외 {len(res[1.5][1]) - 12}건")

    print("\n[3] 최악 낙폭 구간만 — 조기 반응이 낙폭을 줄였나")
    for lo, hi, nm in (("2018-01-01", "2019-01-31", "2018 하락"), ("2020-01-20", "2020-05-29", "2020 코로나"),
                       ("2022-01-01", "2022-12-30", "2022 하락"), ("2024-07-01", "2024-09-30", "2024-08 급락")):
        i0 = next((n for n, d in enumerate(dates) if d >= lo), None)
        i1 = next((n for n, d in enumerate(dates) if d > hi), len(b200))
        if i0 is None or i0 < W or i1 - i0 < 40:
            continue
        a = run_backtest(b200[:i1], blev[:i1], CAP, P, start_index=i0)
        out = [f"{nm}: 현행 {a.equity[-1] / CAP - 1:+.2%} MDD {mdd_of(a.equity):.2%}"]
        for k in (1.5, 3.0):
            bb = run_variant(b200[:i1], blev[:i1], P, i0, mult=k)
            out.append(f"k={k} {bb.equity[-1] / CAP - 1:+.2%} MDD {mdd_of(bb.equity):.2%}")
        print("  " + " | ".join(out))

    print("\n[4] 89개 1년 창 — 정확 차이")
    starts = list(range(W, len(b200) - H, STEP))
    print(f"{'변형':<26}{'평균':>10}{'중앙':>10}{'최악':>10}{'최선':>10}{'이김':>6}{'짐':>5}{'MDD 개선 창':>12}")
    for k in (1.5, 3.0):
        d, dm = [], []
        for st_ in starts:
            aa = run_backtest(b200[:st_ + H], blev[:st_ + H], CAP, P, start_index=st_)
            bb = run_variant(b200[:st_ + H], blev[:st_ + H], P, st_, mult=k)
            d.append(bb.equity[-1] / CAP - aa.equity[-1] / CAP)
            dm.append(mdd_of(bb.equity) - mdd_of(aa.equity))
        print(f"{f'조기 반응 k={k}':<26}{mean(d):>+10.3%}{median(d):>+10.3%}{min(d):>+10.3%}{max(d):>+10.3%}"
              f"{sum(1 for x in d if x > 0):>6}{sum(1 for x in d if x < 0):>5}{sum(1 for x in dm if x > 0):>12}")


if __name__ == "__main__":
    main()
