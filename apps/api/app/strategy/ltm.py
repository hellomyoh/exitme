"""LTM (Leveraged Trend-Momentum) — 미국 지수 매매 공식 (2026-09-06 사용자 채택).

연구: docs/ltv-strategy-study-20260906.md (문헌·4차 개선·민감도). 대상: QQQ(1배) + QLD(2배) 또는 TQQQ(3배).
규칙(일 1회 종가 판정 → 다음 거래일 시가 시장가 체결):
1) 보유 게이트(TF 그대로): 종가 > MA200 → ON, 보유 중 종가 < MA200×(1−2%) → OFF(히스테리시스). OFF 는 전량 현금.
2) 레버리지 허용(ON 일 때 둘 다 만족 → 노출 2.0, 아니면 1.0):
   · 12개월(252거래일) 수익률 > 0
   · 최근 20거래일 안에 1배 종가 일간 −3% 이상 급락일 없음 (급락일마다 20일 연장)
3) 실행: E ≤ 1 → 1배 비중 E. E > 1 → 레버리지 비중 wL=(E−1)/(L−1), 1배 비중 1−wL (QLD: 100% QLD, TQQQ: 50/50).
4) 리밸런스 밴드: 목표 E 와 현재 E 의 차이가 목표의 10% 미만이면 거래 생략. 추세 전환·레버리지 on/off 는 즉시.
비용: 편도 수수료 0.1% + 시장가 슬리피지 0.05%, 보수 QQQ 0.20% / QLD 0.95% / TQQQ 0.84% 일할. 세전·환율 미모델.
레짐 표기: 레버리지 ON = BULL(상승), 1배 보유 = NEUTRAL(중립), 현금 = BEAR(하락).
결과는 BacktestResult 로 반환해 시뮬레이터·일지·실전 전환을 TF/RAVG 와 같은 경로로 재사용한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.strategy.backtest import BacktestResult, Cancelled, ClosedTrade, Fill, compute_kpi
from app.strategy.planner import K200, LEV, Order, Plan
from app.strategy.regime import Regime


@dataclass(frozen=True)
class LTMParams:
    ma_len: int = 200
    exit_buffer: float = 0.02
    mom_days: int = 252
    shock_drop: float = 0.03
    shock_days: int = 20
    e_max: float = 2.0
    band: float = 0.10
    cash_reserve: float = 0.01   # 목표 수량 산정 시 남기는 현금 비율 — 일할 보수(연 ≤0.95%)로 현금이 음수가 되지 않게 (2026-09-06 e2e 결함)
    commission: float = 0.001
    slippage: float = 0.0005
    fee_1x: float = 0.002
    fee_lev: float = 0.0095
    lev_multiple: float = 2.0


LTM_FEES = {"QLD": 0.0095, "TQQQ": 0.0084}
LTM_MULT = {"QLD": 2.0, "TQQQ": 3.0}


def params_for(lev_code: str) -> LTMParams:
    return LTMParams(fee_lev=LTM_FEES.get(lev_code, 0.0095), lev_multiple=LTM_MULT.get(lev_code, 2.0))


def _sma(xs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    acc = 0.0
    for i, x in enumerate(xs):
        acc += x
        if i >= n:
            acc -= xs[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def ltm_states(closes: list[float], p: LTMParams = LTMParams()) -> list[dict | None]:
    """1배 종가 시계열 → 일별 상태 [{on, e_target, ma, mom, shock_left}]. 워밍업 구간은 None.

    신호 엔진(실전 주문표)과 백테스트가 같은 함수를 쓴다 — 규칙은 여기 한 곳에만 있다.
    """
    n = len(closes)
    ma = _sma(closes, p.ma_len)
    out: list[dict | None] = [None] * n
    on = False
    shock_until = -1
    warm = max(p.ma_len, p.mom_days)
    for i in range(n):
        if i >= 1 and closes[i] / closes[i - 1] - 1.0 <= -p.shock_drop:
            shock_until = max(shock_until, i + p.shock_days)
        m = ma[i]
        if m is None or i < warm:
            continue
        c = closes[i]
        if not on and c > m:
            on = True
        elif on and c < m * (1 - p.exit_buffer):
            on = False
        mom = closes[i] / closes[i - p.mom_days] - 1.0
        lever = on and mom > 0 and i > shock_until
        e = 0.0 if not on else (p.e_max if lever else 1.0)
        out[i] = {"on": on, "e_target": e, "ma": m, "mom": mom, "shock_left": max(shock_until - i, 0),
                  "exit_level": m * (1 - p.exit_buffer)}
    return out


def target_weights(e: float, lev_multiple: float) -> tuple[float, float]:
    """노출 E → (1배 비중, 레버리지 비중). 전액 투자 원칙(E>1 이면 현금 0)."""
    if e <= 0:
        return 0.0, 0.0
    if e <= 1.0:
        return e, 0.0
    w_lev = min((e - 1.0) / (lev_multiple - 1.0), 1.0)
    return 1.0 - w_lev, w_lev


def _regime(state: dict | None) -> Regime:
    if state is None or not state["on"]:
        return Regime.BEAR if state is not None else Regime.NEUTRAL
    return Regime.BULL if state["e_target"] > 1.0 else Regime.NEUTRAL


def run_ltm_backtest(bars_1x: list[dict], bars_lev: list[dict], capital: float,
                     p: LTMParams | None = None, start_index: int | None = None,
                     progress_cb=None, initial_lots: list[dict] | None = None) -> BacktestResult:
    """bars_*: 날짜 정렬·교집합 정렬된 [{date, open, high, low, close}] (센트 정수 권장). 정수 주 단위."""
    p = p or LTMParams()
    n = len(bars_1x)
    dates = [b["date"] for b in bars_1x]
    c1 = [float(b["close"]) for b in bars_1x]
    o1 = [float(b["open"]) for b in bars_1x]
    cL = [float(b["close"]) for b in bars_lev]
    oL = [float(b["open"]) for b in bars_lev]
    L = p.lev_multiple
    states = ltm_states(c1, p)

    first = start_index if start_index is not None else 0
    cash = float(capital)
    q1 = qL = 0
    cost1 = costL = 0.0      # 레그별 평균 매입가(수수료 포함)
    buy_i1 = buy_iL = first
    for h in (initial_lots or []):
        if h.get("leg", "K200") == "K200":
            q1 += int(h["qty"]); cost1 = float(h["price"])
        else:
            qL += int(h["qty"]); costL = float(h["price"])
    base_capital = float(capital) + sum(int(h["qty"]) * float(h["price"]) for h in (initial_lots or []))

    trades: list[ClosedTrade] = []
    fills: list[Fill] = []
    plans: list[Plan] = []
    out_dates: list[str] = []
    equity, bench, regimes, exposures = [], [], [], []
    cash_curve, q1_curve, qL_curve = [], [], []
    bench_qty, bench_cash = 0.0, base_capital
    pending: tuple[Order, ...] = ()
    active_start: int | None = None
    total = max(n - 1 - first, 1)

    def exec_orders(orders: tuple[Order, ...], i: int) -> None:
        nonlocal cash, q1, qL, cost1, costL, buy_i1, buy_iL
        # 매도 먼저(현금 확보) → 매수
        for o in sorted(orders, key=lambda x: 0 if x.side == "sell" else 1):
            px_open = o1[i] if o.instrument == K200 else oL[i]
            if o.side == "sell":
                q = min(o.qty, q1 if o.instrument == K200 else qL)
                if q <= 0:
                    continue
                fill = px_open * (1 - p.slippage)
                proceeds = q * fill * (1 - p.commission)
                cost = cost1 if o.instrument == K200 else costL
                bi = buy_i1 if o.instrument == K200 else buy_iL
                trades.append(ClosedTrade(o.instrument, o.kind, q, cost, fill, bi, i, proceeds - q * cost))
                fills.append(Fill(dates[i], o.instrument, "sell", o.kind, round(fill), q))
                cash += proceeds
                if o.instrument == K200:
                    q1 -= q
                else:
                    qL -= q
            else:
                fill = px_open * (1 + p.slippage)
                unit = fill * (1 + p.commission)
                q = min(o.qty, int(cash / unit)) if unit > 0 else 0
                if q <= 0:
                    continue
                cash -= q * unit
                if o.instrument == K200:
                    cost1 = (q1 * cost1 + q * unit) / (q1 + q); q1 += q; buy_i1 = i
                else:
                    costL = (qL * costL + q * unit) / (qL + q); qL += q; buy_iL = i
                fills.append(Fill(dates[i], o.instrument, "buy", o.kind, round(fill), q))

    for i in range(first, n - 1):
        if progress_cb is not None and (i - first) % max(total // 100, 1) == 0:
            if progress_cb(i - first, total) is False:
                raise Cancelled()
        nxt = i + 1
        # ① 전일 계획 체결 (익일 시가)
        if pending:
            exec_orders(pending, nxt)
            pending = ()
        # ② 보수 일할
        if q1 > 0:
            cash -= q1 * c1[nxt] * p.fee_1x / 365.0
        if qL > 0:
            cash -= qL * cL[nxt] * p.fee_lev / 365.0
        # ③ 종가 판정 → 다음 계획
        st = states[nxt]
        v = cash + q1 * c1[nxt] + qL * cL[nxt]
        e_now = ((q1 * c1[nxt] + qL * cL[nxt] * L) / v) if v > 0 else 0.0
        orders: tuple[Order, ...] = ()
        if st is None:
            plans.append(Plan("INSUFFICIENT_HISTORY", Regime.NEUTRAL, 0.0, 0.0, 0.0, (), None, {}))
            regime = "NEUTRAL"
        else:
            if active_start is None:
                active_start = len(equity)
            e_t = st["e_target"]
            prev = states[nxt - 1] if nxt - 1 >= 0 else None
            switched = prev is None or prev["on"] != st["on"] or (prev["e_target"] > 1.0) != (e_t > 1.0)
            if switched or abs(e_t - e_now) >= p.band * max(e_t, 0.5):
                w1, wL = target_weights(e_t, L)
                v_inv = v * (1 - p.cash_reserve)
                t1 = int(w1 * v_inv / c1[nxt]) if c1[nxt] > 0 else 0
                tL = int(wL * v_inv / cL[nxt]) if (wL > 0 and cL[nxt] > 0) else 0
                kind = ("ltm_exit" if not st["on"] else
                        "ltm_entry" if (prev is None or not prev["on"]) else
                        "ltm_lever_on" if (e_t > 1.0 and not (prev["e_target"] > 1.0)) else
                        "ltm_lever_off" if (e_t <= 1.0 and prev["e_target"] > 1.0) else "ltm_rebal")
                olist = []
                for inst, q_now, q_tgt in ((K200, q1, t1), (LEV, qL, tL)):
                    d = q_tgt - q_now
                    if d > 0:
                        olist.append(Order(inst, "buy", "market", d, None, kind))
                    elif d < 0:
                        olist.append(Order(inst, "sell", "market", -d, None, kind))
                orders = tuple(olist)
                pending = orders
            reg = _regime(st)
            regime = reg.value
            w1, wL = target_weights(e_t, L)
            plans.append(Plan("OK", reg, e_t, w1, wL, orders, None,
                              indicators={"close": c1[nxt], "ma200": st["ma"], "gap_to_ma200": c1[nxt] / st["ma"] - 1,
                                          "exit_level": st["exit_level"], "mom12": st["mom"],
                                          "shock_days_left": st["shock_left"], "exposure": e_now}))
            if bench_qty == 0.0 and bench_cash > 0 and nxt + 1 < n:
                bench_qty = bench_cash / o1[nxt + 1]
                bench_cash = 0.0
        out_dates.append(dates[nxt])
        equity.append(v)
        bench.append(bench_cash + bench_qty * c1[nxt] if bench_qty else base_capital)
        regimes.append(regime)
        exposures.append(e_now)
        cash_curve.append(cash)
        q1_curve.append(q1)
        qL_curve.append(qL)

    kpi = compute_kpi(equity[active_start or 0:], base_capital, trades)
    kpi["open_lots"] = (1 if q1 > 0 else 0) + (1 if qL > 0 else 0)
    final_lots = []
    if q1 > 0:
        final_lots.append({"instrument": K200, "qty": q1, "price": int(round(cost1)), "date": dates[buy_i1]})
    if qL > 0:
        final_lots.append({"instrument": LEV, "qty": qL, "price": int(round(costL)), "date": dates[buy_iL]})
    return BacktestResult(out_dates, equity, bench, regimes, exposures, trades, kpi["open_lots"], kpi,
                          plans=plans, fills=fills, cash_curve=cash_curve, qty_200=q1_curve, qty_lev=qL_curve,
                          final_lots=final_lots)
