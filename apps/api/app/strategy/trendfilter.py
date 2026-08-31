"""미국 전용 TF(추세 필터 보유) 전략 — QQQ 단일 종목 (2026-08-31 사용자 승인).

규칙 (docs/us-backtest-20260831.md 후속 실험으로 채택):
- 종가 > MA200 → 다음 거래일 시가에 전량 매수 (tf_entry, 시장가)
- 보유 중 종가 < MA200×(1−2%) → 다음 거래일 시가에 전량 매도 (tf_exit, 시장가) — 히스테리시스
- 그 외 상태 유지. 그리드·레버리지·E 조절 없음 — 연 3회 수준의 전환만 발생.

근거: QQQ 19년 실측에서 RAVG(+720%, 거래 1,155회) 대비 +781%/거래 59회/MDD 동일.
나스닥처럼 추세가 길고 회복이 빠른 시장에서는 '시장에 있는 시간'이 사이징 정교함을 이긴다.
결과는 BacktestResult 로 반환해 시뮬레이터·일지·전환 경로를 그대로 재사용한다.
레짐 표기: 보유 = BULL, 현금 대기 = NEUTRAL.
"""
from __future__ import annotations

from app.strategy.backtest import BacktestResult, ClosedTrade, Fill, compute_kpi
from app.strategy.planner import K200, Order, Plan
from app.strategy.regime import Regime

TF_MA = 200            # 추세 기준선
TF_EXIT_BUFFER = 0.02  # 이탈 히스테리시스 — MA200 을 2% 관통해야 청산
TF_COMMISSION = 0.001  # 편도 수수료 (미국 기본)
TF_FEE_ANNUAL = 0.002  # QQQ 보수 연 0.20% (일할)


def run_tf_backtest(bars: list[dict], capital: float,
                    start_index: int | None = None,
                    commission: float = TF_COMMISSION,
                    fee_annual: float = TF_FEE_ANNUAL,
                    progress_cb=None) -> BacktestResult:
    """bars: [{date, open, high, low, close, volume}] — 센트 정수 스케일 권장."""
    n = len(bars)
    dates = [b["date"] for b in bars]
    closes = [float(b["close"]) for b in bars]
    opens = [float(b["open"]) for b in bars]

    ma200: list[float | None] = [None] * n
    acc = 0.0
    for i, c in enumerate(closes):
        acc += c
        if i >= TF_MA:
            acc -= closes[i - TF_MA]
        if i >= TF_MA - 1:
            ma200[i] = acc / TF_MA

    first = start_index if start_index is not None else 0
    cash, qty = float(capital), 0
    buy_px = 0.0
    buy_i = 0
    trades: list[ClosedTrade] = []
    fills: list[Fill] = []
    plans: list[Plan] = []
    out_dates: list[str] = []
    equity, bench, regimes, exposures = [], [], [], []
    cash_curve, qty_curve = [], []
    bench_qty, bench_cash = 0.0, float(capital)
    pending: str | None = None
    active_start: int | None = None
    total = max(n - 1 - first, 1)

    for i in range(first, n - 1):
        if progress_cb is not None and (i - first) % max(total // 100, 1) == 0:
            if progress_cb(i - first, total) is False:
                from app.strategy.backtest import Cancelled

                raise Cancelled()
        nxt = i + 1
        # ① 전일 계획의 시장가 체결 (익일 시가)
        if pending == "buy":
            px = opens[nxt]
            qty = int(cash / (px * (1 + commission)))
            if qty > 0:
                cash -= qty * px * (1 + commission)
                buy_px, buy_i = px, nxt
                fills.append(Fill(dates[nxt], K200, "buy", "tf_entry", round(px), qty))
        elif pending == "sell" and qty > 0:
            px = opens[nxt]
            proceeds = qty * px * (1 - commission)
            pnl = (px - buy_px) * qty - qty * px * commission - qty * buy_px * commission
            trades.append(ClosedTrade(K200, "tf", qty, buy_px, px, buy_i, nxt, pnl))
            fills.append(Fill(dates[nxt], K200, "sell", "tf_exit", round(px), qty))
            cash += proceeds
            qty = 0
        pending = None

        # ② 보수 일할 차감 (보유 평가액 기준)
        if qty > 0:
            cash -= qty * closes[nxt] * fee_annual / 365.0

        # ③ 당일 종가 기준 다음 계획
        m = ma200[nxt]
        c = closes[nxt]
        orders: tuple[Order, ...] = ()
        if m is None:
            plans.append(Plan("INSUFFICIENT_HISTORY", Regime.NEUTRAL, 0.0, 0.0, 0.0, (), None, {}))
            regime = "NEUTRAL"
        else:
            if active_start is None:
                active_start = len(equity)
            holding = qty > 0
            if not holding and c > m:
                est = int(cash / c)
                if est > 0:
                    orders = (Order(K200, "buy", "market", est, None, "tf_entry"),)
                    pending = "buy"
            elif holding and c < m * (1 - TF_EXIT_BUFFER):
                orders = (Order(K200, "sell", "market", qty, None, "tf_exit"),)
                pending = "sell"
            # 벤치마크 최초 진입 (전략과 동일 규칙: 다음 거래일 시가)
            regime = "BULL" if (holding or pending == "buy") else "NEUTRAL"
            plans.append(Plan(
                "OK", Regime.BULL if regime == "BULL" else Regime.NEUTRAL,
                1.0 if regime == "BULL" else 0.0, 1.0 if regime == "BULL" else 0.0, 0.0,
                orders, None,
                indicators={"close": c, "ma200": m, "gap_to_ma200": c / m - 1,
                            "exit_level": m * (1 - TF_EXIT_BUFFER)},
            ))
            if bench_qty == 0.0 and bench_cash > 0 and nxt + 1 < n:
                bench_qty = bench_cash / opens[nxt + 1]
                bench_cash = 0.0

        v = cash + qty * c
        bench_v = bench_cash + bench_qty * c * (1 - 0)  # 보수는 전략과 동일하게 미차감(단순 비교)
        out_dates.append(dates[nxt])
        equity.append(v)
        bench.append(bench_v if bench_qty else float(capital))
        regimes.append(regime)
        exposures.append(1.0 if qty > 0 else 0.0)
        cash_curve.append(cash)
        qty_curve.append(qty)

    kpi = compute_kpi(equity[active_start or 0:], capital, trades)
    kpi["open_lots"] = 1 if qty > 0 else 0
    final_lots = ([{"instrument": K200, "qty": qty, "price": int(round(buy_px)), "date": dates[buy_i]}]
                  if qty > 0 else [])
    return BacktestResult(out_dates, equity, bench, regimes, exposures, trades,
                          kpi["open_lots"], kpi, plans=plans, fills=fills,
                          cash_curve=cash_curve, qty_200=qty_curve,
                          qty_lev=[0] * len(qty_curve), final_lots=final_lots)
