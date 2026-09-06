"""라오어 무한매수법 v2.2 / v3.0 · 밸류 리밸런싱(VR) — 비교 검증용 재현 엔진 (2026-09-06 지시).

LTM 연구(ltv-strategy-study-20260906.md)와 **같은 조건**(같은 봉·수수료 0.1%·세전·환율 미모델)으로 돌려 비교한다.
규칙 출처: pbdfinance "무한매수법 v2.2 / v3.0 방법론", quantstack "VR 개요"(V←V+P/G, 밴드 ±15%, 2주, 거치식 풀 한도 50%).
공식 시트가 아니라 공개 규칙 요약을 재현한 것이므로 세부(체결 순서·반올림)는 근사다 — 문서에 가정을 적는다.

체결 모델: LOC 매수 = 종가 ≤ 지정가면 종가 체결, LOC 매도 = 종가 ≥ 지정가면 종가 체결, 지정가 매도 = 당일 고가 ≥ 지정가면
지정가 체결(같은 날 LOC 와 동시 성립하면 지정가 먼저), MOC = 종가. 수수료 편도 0.1%, 소수 주 허용(비교 실험).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.strategy.backtest import ClosedTrade, compute_kpi
from app.strategy.ltv import LTVResult


@dataclass(frozen=True)
class InfiniteParams:
    splits: int = 40             # v2.2: 40 / v3.0: 20
    tp_pct: float = 0.10         # 3/4 지정가 익절 목표 (v2.2 TQQQ +10%, v3.0 +15%)
    half_t: float = 20.0         # 전반전 기준 T (v2.2 20 / v3.0 19)
    big_a: float = 10.0          # 큰수/부분매도 LOC 공식 a − b·T (%): v2.2 10 − T/2, v3.0 15 − 1.5T
    big_b: float = 0.5
    compound: bool = False       # v3.0: 실현 수익을 원금에 합산해 1회 매수액 갱신
    quarter_mode_t: float | None = None  # v3.0: T ≥ 19 구간 쿼터모드 (1/4 MOC 매도, 3/4 지정가 −15%)
    quarter_stop_pct: float = 0.15       # v3.0 쿼터모드 3/4 손절 지정가 (−15%)
    commission: float = 0.001
    fee_annual: float = 0.0084   # TQQQ 보수


V22 = InfiniteParams()
V30 = InfiniteParams(splits=20, tp_pct=0.15, half_t=19.0, big_a=15.0, big_b=1.5, compound=True, quarter_mode_t=19.0)


def run_infinite(bars: list[dict], capital: float, p: InfiniteParams = V22, label: str = "무한매수 v2.2") -> LTVResult:
    """bars: [{date, open, high, low, close}]. 사이클 = 첫 매수 ~ 전량 매도. 단리(v2.2)는 실현 수익을 현금에 쌓아 둔다."""
    n = len(bars)
    dates = [b["date"] for b in bars]
    C = float(capital)          # 사이클 원금 (v2.2 고정, v3.0 복리)
    A = C / p.splits            # 1회 매수액
    cash = float(capital)       # 총 현금 (원금 + 누적 수익)
    qty = 0.0
    cost = 0.0                  # 보유 원가 합 (평단 = cost/qty)
    trades: list[ClosedTrade] = []
    equity: list[float] = []
    exposure: list[float] = []
    on: list[bool] = []
    cycles = 0
    buy_days = 0
    cycle_start_i = 0

    def buy(amount: float, px: float, i: int) -> None:
        nonlocal cash, qty, cost
        amount = min(amount, cash / (1 + p.commission))
        if amount <= 0 or px <= 0:
            return
        q = amount / px
        cash -= amount * (1 + p.commission)
        qty += q
        cost += amount

    def sell(q: float, px: float, i: int, kind: str) -> None:
        nonlocal cash, qty, cost
        q = min(q, qty)
        if q <= 0:
            return
        avg = cost / qty if qty > 0 else px
        proceeds = q * px * (1 - p.commission)
        cash += proceeds
        trades.append(ClosedTrade("lev", kind, int(round(q)), avg, px, cycle_start_i, i, proceeds - q * avg))
        cost -= q * avg
        qty -= q
        if qty < 1e-9:
            qty, cost = 0.0, 0.0

    for i in range(n):
        c, hi = float(bars[i]["close"]), float(bars[i]["high"])
        if qty > 0:
            cash -= qty * c * p.fee_annual / 365.0
        if qty <= 0:
            # 사이클 시작 — 첫 회차는 종가에 1회 매수액 전액
            cycles += 1
            cycle_start_i = i
            if p.compound:
                A = C / p.splits
            buy(A, c, i)
            buy_days = 1
        else:
            avg = cost / qty
            T = cost / A                                  # 진행 회차(누적 매수액 / 1회 매수액)
            big = avg * (1 + (p.big_a - p.big_b * T) / 100.0)
            sold_all = False
            if p.quarter_mode_t is not None and T >= p.quarter_mode_t and cash < A:
                # v3.0 쿼터모드: 1/4 MOC 매도, 3/4 는 −15% 지정가 손절 대기
                sell(qty * 0.25, c, i, "quarter")
                if float(bars[i]["low"]) <= avg * (1 - p.quarter_stop_pct):
                    sell(qty, avg * (1 - p.quarter_stop_pct), i, "stop")
                    sold_all = True
            else:
                # ── 매도: 3/4 지정가 +tp, 1/4 LOC big (같은 날이면 지정가 먼저)
                tp_px = avg * (1 + p.tp_pct)
                if hi >= tp_px:
                    sell(qty * 0.75, tp_px, i, "tp")
                if qty > 0 and c >= big:
                    sell(qty * 0.25 if not (hi >= tp_px) else qty, c, i, "loc_sell")
                if hi >= tp_px and qty > 0:
                    sell(qty, c, i, "tp_rest")   # 3/4 익절 체결일엔 잔여도 정리(사이클 종료)
                    sold_all = True
                if qty <= 0:
                    sold_all = True
            if sold_all or qty <= 0:
                if p.compound:
                    realized = cash - C
                    if realized > 0:
                        C = cash                        # 수익 합산 → 다음 사이클 1회 매수액 증가
                        A = C / p.splits
                qty, cost = 0.0, 0.0
            else:
                # ── 매수 (LOC, 종가 체결 판정)
                if cash < A * 0.5:
                    # v2.2 쿼터손절: 시드 소진(현금 기준 — 수수료로 T 가 40 에 못 미쳐도 소진) → 보유 1/4 MOC 매도로 시드 재확보 후 계속
                    if p.quarter_mode_t is None:
                        sell(qty * 0.25, c, i, "quarter")
                        buy_days = 0
                else:
                    if T < p.half_t:
                        if c <= avg:
                            buy(A / 2, c, i)
                        if c <= big:
                            buy(A / 2, c, i)
                    else:
                        if c <= big:
                            buy(A, c, i)
                    buy_days += 1
        v = cash + qty * c
        equity.append(v)
        exposure.append((qty * c) / v if v > 0 else 0.0)
        on.append(qty > 0)

    kpi = compute_kpi(equity, capital, trades)
    years = max(len(equity) / 252.0, 1e-9)
    return LTVResult(dates, equity, exposure, on, trades, kpi, cycles, len(trades),
                     turnover=sum(abs(t.qty * t.sell_price) for t in trades) / (sum(equity) / len(equity)) / years,
                     time_in_market=sum(1 for x in on if x) / len(on),
                     avg_exposure=sum(exposure) / len(exposure), label=label)


@dataclass(frozen=True)
class VRParams:
    band: float = 0.15           # 밴드 ±15%
    cycle_days: int = 10         # 2주 = 10거래일
    g: float = 10.0              # V ← V + P/G
    init_stock: float = 0.5      # 초기 주식 비율 (거치식 가정 50%, 나머지 풀)
    pool_use_limit: float = 0.5  # 매수 시 풀 사용 한도 (거치식 50%)
    commission: float = 0.001
    fee_annual: float = 0.0084


def run_vr(bars: list[dict], capital: float, p: VRParams = VRParams(), label: str = "VR G10") -> LTVResult:
    """밸류 리밸런싱(거치식): 2주마다 V 갱신 후 평가금이 밴드 밖이면 V 로 되돌린다(매수는 풀 한도 내)."""
    n = len(bars)
    dates = [b["date"] for b in bars]
    c0 = float(bars[0]["close"])
    V = capital * p.init_stock
    pool = capital * (1 - p.init_stock)
    qty = V / c0
    pool -= 0.0
    cash = pool - V * p.commission          # 초기 매수 수수료
    cost = V
    trades: list[ClosedTrade] = []
    equity, exposure, on = [], [], []
    rebal = 0
    for i in range(n):
        c = float(bars[i]["close"])
        if qty > 0:
            cash -= qty * c * p.fee_annual / 365.0
        if i > 0 and i % p.cycle_days == 0:
            V = V + cash / p.g                  # V 갱신 (풀/G) — 풀이 줄면 기울기도 완만해짐
            value = qty * c
            if value > V * (1 + p.band):
                sell_amt = value - V
                q = sell_amt / c
                avg = cost / qty
                cash += sell_amt * (1 - p.commission)
                trades.append(ClosedTrade("lev", "vr_sell", int(round(q)), avg, c, 0, i, sell_amt * (1 - p.commission) - q * avg))
                cost -= q * avg
                qty -= q
                rebal += 1
            elif value < V * (1 - p.band):
                buy_amt = min(V - value, cash * p.pool_use_limit / (1 + p.commission))
                if buy_amt > 0:
                    cash -= buy_amt * (1 + p.commission)
                    qty += buy_amt / c
                    cost += buy_amt
                    rebal += 1
        v = cash + qty * c
        equity.append(v)
        exposure.append((qty * c) / v if v > 0 else 0.0)
        on.append(qty > 0)
    kpi = compute_kpi(equity, capital, trades)
    years = max(len(equity) / 252.0, 1e-9)
    return LTVResult(dates, equity, exposure, on, trades, kpi, 0, rebal,
                     turnover=sum(abs(t.qty * t.sell_price) for t in trades) / (sum(equity) / len(equity)) / years,
                     time_in_market=1.0, avg_exposure=sum(exposure) / len(exposure), label=label)
