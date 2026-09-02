"""백테스트 시뮬레이터 — 종가 신호 → 익일 체결 (feature-backtest.md §5 확정 규칙).

체결 규칙(§5.1): 매수 지정가 L — Open≤L→Open / Low≤L<Open→L(동가 포함) / 미달 미체결.
매도 대칭. 갭 필터 우선(당일 그리드 체결 0건). 시장가성 주문은 시가 ± slippage.
비용(§5.2): 수수료 전 체결, 레버리지 실현이익 15.4%, 보수 일할(달력일/365).
KPI(§5.3): CAGR 252, MDD 종가, 샤프 rf=0·ddof=1, 거래 1건 = FIFO 라운드트립.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.strategy.params import Params
from app.strategy.planner import (
    K200,
    LEV,
    Lot,
    Market,
    Order,
    Plan,
    Portfolio,
    apply_regime_conversion,
    grid_ratio,
    plan,
    prepare,
)
from app.strategy.regime import Regime


@dataclass
class ClosedTrade:
    instrument: str
    kind: str
    qty: int
    buy_price: float
    sell_price: float
    buy_index: int
    sell_index: int
    pnl: float          # 비용 차감 후


@dataclass
class Fill:
    """개별 체결 기록 — 일자별 매매 저널용."""

    date: str
    instrument: str
    side: str      # buy | sell
    kind: str
    price: int
    qty: int


@dataclass
class BacktestResult:
    dates: list[str]
    equity: list[float]
    benchmark: list[float]
    regimes: list[str]
    exposures: list[float]
    trades: list[ClosedTrade]
    open_lots: int
    kpi: dict
    plans: list[Plan] = field(default_factory=list)   # 재현성 검증용 (시그널 엔진 대조)
    fills: list[Fill] = field(default_factory=list)   # 일자별 체결 저널
    cash_curve: list[float] = field(default_factory=list)
    qty_200: list[int] = field(default_factory=list)
    qty_lev: list[int] = field(default_factory=list)
    final_lots: list[dict] = field(default_factory=list)  # 실전 전환 시드용


def _fee(value: float, annual_rate: float, days: float) -> float:
    return value * annual_rate * days / 365.0


def _fill_market(open_px: float, side: str, slippage: float) -> float:
    return open_px * (1 + slippage) if side == "buy" else open_px * (1 - slippage)


def _fill_limit_buy(limit: int, open_px: float, low: float) -> float | None:
    if open_px <= limit:
        return open_px
    if low <= limit:
        return float(limit)
    return None


def _fill_limit_sell(limit: int, open_px: float, high: float) -> float | None:
    if open_px >= limit:
        return open_px
    if high >= limit:
        return float(limit)
    return None


class _Ledger:
    """FIFO 원장 — 매도 체결을 로트에 FIFO 매칭해 라운드트립을 만든다."""

    def __init__(self, params: Params):
        self.params = params
        self.closed: list[ClosedTrade] = []

    def sell(self, pf: Portfolio, instrument: str, qty: int, price: float, i: int,
             kinds: tuple[str, ...] | None = None, lot: Lot | None = None) -> float:
        """매도 체결 적용. 반환: 현금 유입(수수료·세금 차감 후)."""
        remaining = qty
        proceeds = 0.0
        targets = [lot] if lot is not None else [
            l for l in pf.lots if l.instrument == instrument and (kinds is None or l.kind in kinds)
        ]
        for l in targets:
            if remaining <= 0:
                break
            take = min(l.qty, remaining)
            if take <= 0:
                continue
            gross = take * price
            commission = gross * self.params.commission
            tax = 0.0
            if instrument == LEV:  # 보유기간과세 단순화 — 실현이익 × 15.4% (손실 0)
                gain = (price - l.price) * take
                tax = max(gain, 0.0) * self.params.lev_tax
            # 매수 수수료(주당)도 라운드트립에 귀속 — §5.2 양방향 수수료 (검증 B1)
            pnl = (price - l.price) * take - commission - take * l.fee_ps - tax
            self.closed.append(ClosedTrade(instrument, l.kind, take, l.price, price, l.buy_index, i, pnl))
            proceeds += gross - commission - tax
            l.qty -= take
            remaining -= take
        pf.lots = [l for l in pf.lots if l.qty > 0]
        return proceeds


class Cancelled(RuntimeError):
    """사용자 취소 — 부분 결과를 저장하지 않는다 (feature-backtest §8)."""


def run_backtest(bars_200: list[dict], bars_lev: list[dict], capital: float,
                 params: Params, start_index: int | None = None,
                 collect_plans: bool = False, progress_cb=None,
                 plan_final: bool = False,
                 initial_lots: list[dict] | None = None) -> BacktestResult:
    """bars: [{date, open, high, low, close, volume}] 두 시계열은 날짜 정렬·동일 길이 가정."""
    if len(bars_200) != len(bars_lev):
        raise ValueError("bars_200 and bars_lev must be aligned")
    dates = [b["date"] for b in bars_200]

    def to_market(bars: list[dict]) -> Market:
        return prepare(
            [float(b["open"]) for b in bars], [float(b["high"]) for b in bars],
            [float(b["low"]) for b in bars], [float(b["close"]) for b in bars], params,
        )

    m200, mlev = to_market(bars_200), to_market(bars_lev)
    pf = Portfolio(cash=capital)
    # 보유 상태로 시작 (2026-09-02 지시) — 실전 '보유분 입력'과 동일 의미론: 자본금 = 현금, 보유는 별도
    first0 = start_index if start_index is not None else 0
    for h in (initial_lots or []):
        leg = K200 if h.get("leg", "K200") == "K200" else LEV
        kind = "core" if leg == K200 else "lev_strat"
        pf.lots.append(Lot(leg, int(h["qty"]), int(round(float(h["price"]))), kind, None, first0))
    # 수익률 기준 원금 = 현금 + 보유 원가 — 실전 '시작 입금(현금+보유 원가)' 규약과 동일.
    # 현금만 분모로 쓰면 보유 평가액이 통째로 수익으로 잡힘 (2026-09-02 결함: +153% 사례)
    base_capital = capital + sum(int(h["qty"]) * float(h["price"]) for h in (initial_lots or []))
    ledger = _Ledger(params)
    regime = Regime.NEUTRAL
    first = start_index if start_index is not None else 0

    equity_curve: list[float] = []
    bench_curve: list[float] = []
    regimes: list[str] = []
    exposures: list[float] = []
    plans: list[Plan] = []
    out_dates: list[str] = []
    fills: list[Fill] = []
    cash_curve: list[float] = []
    qty_200_curve: list[int] = []
    qty_lev_curve: list[int] = []

    # 벤치마크: KODEX 200 매수보유 (보수 반영, feature-backtest §5.3) — 전략과 같은 총원금으로 시작
    bench_qty = 0.0
    bench_cash = base_capital

    prev_grid = 0.0
    active_start: int | None = None  # 첫 OK 계획 시점 — KPI 는 활동 구간 기준 (검증 B4)
    total = max(len(dates) - 1 - first, 1)
    for i in range(first, len(dates) - 1):
        if progress_cb is not None and (i - first) % max(total // 100, 1) == 0:
            if progress_cb(i - first, total) is False:
                raise Cancelled()
        p = plan(i, m200, mlev, regime, pf, params)
        if collect_plans:
            plans.append(p)
        nxt = i + 1
        o_, h_, l_ = m200.opens[nxt], m200.highs[nxt], m200.lows[nxt]
        lo_, lh_, ll_ = mlev.opens[nxt], mlev.highs[nxt], mlev.lows[nxt]

        if p.status == "OK":
            if active_start is None:
                active_start = len(equity_curve)
            # 레짐 전환 → 로트 재분류 (전환일 종가 기준)
            grid_today = grid_ratio(m200.atr20[i], m200.closes[i], params)
            apply_regime_conversion(pf, regime, p.regime, grid_today, m200.closes[i], params)
            regime = p.regime
            prev_grid = grid_today

            # 벤치마크 최초 진입 — 전략과 동일하게 익일 시가 체결 (§5.1, 검증 B5)
            if bench_qty == 0.0 and bench_cash > 0:
                bench_qty = bench_cash / m200.opens[nxt]
                bench_cash = 0.0

            lot_snapshot = list(pf.lots)  # tp lot_id 참조 안정화

            # ① 시장가 매도 (청산·축소·전술 이탈)
            for od in p.orders:
                if od.otype != "market" or od.side != "sell":
                    continue
                open_px = lo_ if od.instrument == LEV else o_
                px = _fill_market(open_px, "sell", params.slippage_market)
                kinds = None
                if od.kind == "lev_tact_exit":
                    kinds = ("lev_tact1", "lev_tact2")
                elif od.kind == "lev_strat":
                    kinds = ("lev_strat",)
                pf.cash += ledger.sell(pf, od.instrument, od.qty, px, nxt, kinds=kinds)
                fills.append(Fill(dates[nxt], od.instrument, "sell", od.kind, round(px), od.qty))

            # ② 시장가 매수 (레버리지) — 슬리피지는 시장가성 '청산'에만 적용 (§5.2, 검증 B8)
            for od in p.orders:
                if od.otype != "market" or od.side != "buy":
                    continue
                px = lo_
                cost = od.qty * px * (1 + params.commission)
                if cost <= pf.cash and od.qty > 0:
                    pf.cash -= cost
                    pf.lots.append(Lot(LEV, od.qty, int(round(px)), od.kind, None, nxt,
                                       fee_ps=px * params.commission))
                    fills.append(Fill(dates[nxt], LEV, "buy", od.kind, round(px), od.qty))

            # ③ 그리드 지정가 매수 — 갭 필터 우선 (§5.1)
            gap_hit = (params.flags.f5_gap_filter and p.gap_cancel_exact is not None
                       and o_ <= p.gap_cancel_exact)
            if not gap_hit:
                for od in p.orders:
                    if od.otype != "limit" or od.side != "buy":
                        continue
                    px = _fill_limit_buy(od.price, o_, l_)
                    if px is None:
                        continue
                    cost = od.qty * px * (1 + params.commission)
                    if cost > pf.cash:
                        continue
                    pf.cash -= cost
                    fills.append(Fill(dates[nxt], K200, "buy", od.kind, round(px), od.qty))
                    tp = None
                    kind = "grid"
                    if regime is Regime.BULL and params.flags.f1_no_tp_in_bull:
                        kind = "core"     # 상승장 체결분 = 코어 (익절 없음)
                    else:
                        from app.strategy.params import round_tick
                        tp = round_tick(px * (1 + prev_grid), params.tick, up=True)
                    pf.lots.append(Lot(K200, od.qty, int(round(px)), kind, tp, nxt,
                                       fee_ps=px * params.commission))

            # ④ 익절 지정가 매도
            for od in p.orders:
                if od.otype != "limit" or od.side != "sell" or od.lot_id is None:
                    continue
                lot = lot_snapshot[od.lot_id]
                if not any(l is lot for l in pf.lots) or lot.qty <= 0:  # identity 비교 (검증 B13)
                    continue
                px = _fill_limit_sell(od.price, o_, h_)
                if px is None:
                    continue
                qty_sold = min(od.qty, lot.qty)  # 주문 수량 존중 — earmark 초과 매도 방지 (검증 ⑦)
                pf.cash += ledger.sell(pf, K200, qty_sold, px, nxt, lot=lot)
                fills.append(Fill(dates[nxt], K200, "sell", "tp", round(px), qty_sold))
        else:
            regime = Regime.NEUTRAL

        # ⑤ 보수 일할 차감 (달력일 기준)
        days = _calendar_days(dates[i], dates[nxt])
        v200 = pf.value(K200, m200.closes[nxt])
        vlev = pf.value(LEV, mlev.closes[nxt])
        pf.cash -= _fee(v200, params.fee_200, days) + _fee(vlev, params.fee_lev, days)

        # ⑥ 종가 평가
        eq = pf.equity(m200.closes[nxt], mlev.closes[nxt])
        equity_curve.append(eq)
        bench_val = bench_cash + bench_qty * m200.closes[nxt]
        if bench_qty:
            fee_b = _fee(bench_qty * m200.closes[nxt], params.fee_200, days)
            bench_cash -= fee_b
            bench_val -= fee_b
        bench_curve.append(bench_val if bench_qty else base_capital)
        regimes.append(regime.value)
        exposures.append(p.e_target if p.status == "OK" else 0.0)
        out_dates.append(dates[nxt])
        cash_curve.append(pf.cash)
        qty_200_curve.append(sum(l.qty for l in pf.lots if l.instrument == K200))
        qty_lev_curve.append(sum(l.qty for l in pf.lots if l.instrument == LEV))

    if plan_final:
        # 마지막 바(최신 종가) 기준 계획 — 일일 시그널 엔진용. 체결은 하지 않는다.
        # 백테스트를 d+1개 바로 절단 실행하면 이 계획이 전체 실행의 plans[d]와 동일하다 (ADR-005).
        last = len(dates) - 1
        p_final = plan(last, m200, mlev, regime, pf, params)
        plans.append(p_final)

    kpi = compute_kpi(equity_curve[active_start or 0:], base_capital, ledger.closed)
    kpi["open_lots"] = len(pf.lots)  # 미청산 별도 표기 (§5.3, 검증 B6)
    final_lots = [
        {"instrument": l.instrument, "qty": l.qty, "price": l.price,
         "date": dates[min(l.buy_index, len(dates) - 1)]}
        for l in pf.lots
    ]
    return BacktestResult(out_dates, equity_curve, bench_curve, regimes, exposures,
                          ledger.closed, len(pf.lots), kpi, plans,
                          fills, cash_curve, qty_200_curve, qty_lev_curve, final_lots)


def _calendar_days(d1: str, d2: str) -> float:
    from datetime import date

    a = date.fromisoformat(d1)
    b = date.fromisoformat(d2)
    return float((b - a).days) or 1.0


def compute_kpi(equity: list[float], capital: float, trades: list[ClosedTrade]) -> dict:
    if not equity:
        return {"total_return": 0.0, "cagr": None, "mdd": 0.0, "sharpe": None,
                "trades": 0, "win_rate": None, "profit_factor": None, "open_lots_note": None}
    final = equity[-1]
    total_return = final / capital - 1.0
    n = len(equity)
    if final <= 0:
        cagr = None  # 전액 손실 이하 — 거듭제곱이 복소수가 됨 (검증 B7)
    else:
        cagr = (final / capital) ** (252.0 / n) - 1.0 if n >= 252 else None  # 1년 미만 → 누적만 (§5.3)

    peak = capital
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)

    rets = []
    prev = capital
    for v in equity:
        rets.append(v / prev - 1.0)
        prev = v
    sharpe = None
    if len(rets) > 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        sd = var ** 0.5
        sharpe = (mu / sd) * (252 ** 0.5) if sd > 0 else None

    closed = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / closed if closed else None
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    return {"total_return": total_return, "cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "trades": closed, "win_rate": win_rate, "profit_factor": profit_factor}
