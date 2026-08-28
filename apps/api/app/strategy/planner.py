"""RAVG v2 플래너 — 하루치 신호·주문표 생성 (순수 함수, ADR-005).

백테스트와 일일 시그널 엔진이 이 모듈을 공유한다.
규칙 정본: SOURCES/trade_algorithm_final.md / 구현 확정: features/feature-strategy-engine.md §5.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from app.strategy import indicators as ind
from app.strategy.params import Params, round_tick
from app.strategy.regime import Regime, next_regime

K200 = "K200"
LEV = "LEV"


class EngineError(RuntimeError):
    """NaN 등 수치 붕괴 — 조용한 오작동 대신 배치 실패 (feature-strategy-engine §5.3)."""


@dataclass
class Lot:
    instrument: str          # K200 | LEV
    qty: int
    price: int               # 체결가 (호가 단위)
    kind: str                # grid | core | lev_strat | lev_tact1 | lev_tact2
    tp_price: int | None     # 중립 익절가 — 체결 시점 스냅샷 (grid만)
    buy_index: int           # 체결 바 인덱스 (거래일지용)


@dataclass
class Portfolio:
    cash: float
    lots: list[Lot] = field(default_factory=list)

    def value(self, instrument: str, price: float) -> float:
        return sum(l.qty * price for l in self.lots if l.instrument == instrument)

    def equity(self, px_200: float, px_lev: float) -> float:
        return self.cash + self.value(K200, px_200) + self.value(LEV, px_lev)


@dataclass(frozen=True)
class Order:
    instrument: str
    side: str        # buy | sell
    otype: str       # limit | market
    qty: int
    price: int | None   # limit 지정가 (market 은 None)
    kind: str        # grid1|grid2|grid3 | tp | reduce | lev_strat | lev_tact1 | lev_tact2 | lev_liq
    lot_id: int | None = None  # tp 매도의 대상 로트 인덱스


@dataclass(frozen=True)
class Plan:
    status: str                  # OK | INSUFFICIENT_HISTORY
    regime: Regime
    e_target: float
    w_200: float
    w_lev: float
    orders: tuple[Order, ...]
    gap_cancel_below: int | None    # 시가가 이 값 이하면 그리드 매수 전량 취소 (조건부 지시문)
    indicators: dict


@dataclass
class Market:
    """지표 사전 계산 컨테이너 — 모든 지표는 인과적(trailing)이라 인덱스 i까지의 값만 사용된다."""

    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    ma20: list[float | None]
    ma60: list[float | None]
    ma200: list[float | None]
    ema20: list[float | None]
    atr20: list[float | None]
    sigma20: list[float | None]
    sigma_down: list[float | None]
    sigma_ref: list[float | None]


def prepare(opens, highs, lows, closes, params: Params) -> Market:
    sigma_down = ind.rolling_vol_annualized(closes, 20, downside_only=True)
    n = params.sigma_ref_window
    sigma_ref: list[float | None] = [None] * len(closes)
    window: list[float] = []
    for i, v in enumerate(sigma_down):
        if v is not None:
            window.append(v)
            if len(window) > n:
                window.pop(0)
            if len(window) == n:
                s = sorted(window)
                mid = n // 2
                sigma_ref[i] = (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]
    return Market(
        opens=opens, highs=highs, lows=lows, closes=closes,
        ma20=ind.sma(closes, 20), ma60=ind.sma(closes, 60), ma200=ind.sma(closes, 200),
        ema20=ind.ema(closes, 20), atr20=ind.atr(highs, lows, closes, 20),
        sigma20=ind.rolling_vol_annualized(closes, 20),
        sigma_down=sigma_down, sigma_ref=sigma_ref,
    )


def grid_ratio(atr: float, close: float, params: Params) -> float:
    return min(max(params.grid_coef * atr / close, params.grid_min), params.grid_max)


def apply_regime_conversion(pf: Portfolio, old: Regime, new: Regime, grid_today: float, close: float,
                            params: Params) -> None:
    """레짐 전환 시 로트 재분류 (feature-strategy-engine §5.6).

    - → BULL: grid 로트를 core 로 전환(익절 제거)  [f1 on 일 때]
    - BULL → NEUTRAL: core 로트에 전환일 기준 익절가 부여 후 grid 로 편입
    """
    if old is new:
        return
    if new is Regime.BULL and params.flags.f1_no_tp_in_bull:
        for l in pf.lots:
            if l.instrument == K200 and l.kind == "grid":
                l.kind = "core"
                l.tp_price = None
    if old is Regime.BULL and new is not Regime.BULL:
        tp = round_tick(close * (1 + grid_today), params.tick, up=True)
        for l in pf.lots:
            if l.instrument == K200 and l.kind == "core":
                l.kind = "grid"
                l.tp_price = tp


def plan(i: int, m200: Market, mlev: Market, prev_regime: Regime, pf: Portfolio,
         params: Params) -> Plan:
    """인덱스 i(종가 확정)에서 다음 거래일 주문표를 생성한다. pf 는 변경하지 않는다."""
    f = params.flags
    # ── 워밍업 가드 (§5.2)
    if i + 1 < params.min_history or m200.ma200[i] is None or m200.sigma_ref[i] is None:
        return Plan("INSUFFICIENT_HISTORY", Regime.NEUTRAL, 0.0, 0.0, 0.0, (), None, {})

    close = m200.closes[i]
    ma20, ma60, ma200 = m200.ma20[i], m200.ma60[i], m200.ma200[i]
    atr = m200.atr20[i]
    sigma20 = m200.sigma20[i]

    # ── 레짐 (§5.4)
    ma60_prev = m200.ma60[i - params.slope_lookback_v1] if i >= params.slope_lookback_v1 else None
    regime = next_regime(prev_regime, close, ma20, ma60, ma200, params, ma60_prev_slope=ma60_prev)

    # ── 노출 E (§5.3, 정본 §5.1)
    if f.f2_downside_vol:
        sd = max(m200.sigma_down[i], params.sigma_down_floor)
        sref = max(m200.sigma_ref[i], params.sigma_down_floor)
        e_raw = params.blend_abs * (params.target_downside_vol / sd) + (1 - params.blend_abs) * (sref / sd)
    else:
        e_raw = params.target_total_vol_v1 / max(sigma20, params.sigma_down_floor)
    if math.isnan(e_raw) or math.isinf(e_raw):
        raise EngineError(f"E computation degenerate at index {i}")
    emax_bull = params.emax_bull if f.f4_leverage else 1.0
    emax = {Regime.BULL: emax_bull, Regime.NEUTRAL: params.emax_neutral, Regime.BEAR: params.emax_bear}[regime]
    e = min(emax, e_raw)
    w_lev = max(0.0, e - 1.0) if f.f4_leverage else 0.0
    w_200 = min(e, 2.0 - e) if f.f4_leverage else e

    lev_close = mlev.closes[i]
    equity = pf.equity(close, lev_close)
    usable = equity * (1 - params.cash_buffer)
    value_200 = pf.value(K200, close)
    target_200 = w_200 * usable

    orders: list[Order] = []
    grid = grid_ratio(atr, close, params)

    # ── 레버리지 청산 최우선 (정본 §9 / §7): 레짐 이탈 또는 σ20 초과
    lev_lots = [l for l in pf.lots if l.instrument == LEV]
    force_liq = (regime is not Regime.BULL) or (sigma20 is not None and sigma20 > params.sigma20_liquidate)
    if lev_lots and f.f4_leverage and force_liq:
        qty = sum(l.qty for l in lev_lots)
        orders.append(Order(LEV, "sell", "market", qty, None, "lev_liq"))
        lev_lots = []

    # ── K200 매도
    planned_sell_value = 0.0
    if regime is Regime.NEUTRAL or not f.f1_no_tp_in_bull:
        # 중립 왕복 익절 (f1 off 이면 v1: 상승장에도 익절)
        if regime is not Regime.BEAR:
            for idx, l in enumerate(pf.lots):
                if l.instrument == K200 and l.kind == "grid" and l.tp_price:
                    orders.append(Order(K200, "sell", "limit", l.qty, l.tp_price, "tp", lot_id=idx))
    excess = value_200 - target_200
    if excess > params.band * equity:
        # 축소 매도(하락장 신속 축소 / E 하락 리밸런싱) — 밴드 초과 시에만 (§5.5)
        qty = int(excess / close)
        if qty > 0:
            orders.append(Order(K200, "sell", "market", qty, None, "reduce"))
            planned_sell_value += qty * close

    # ── K200 그리드 매수 (하락장 정지)
    gap_cancel_below: int | None = None
    if regime is not Regime.BEAR:
        if f.f5_gap_filter:
            remaining = max(0.0, target_200 - value_200)   # 잔여예산 규칙
            gap_cancel_below = round_tick(close - params.gap_atr_mult * atr, params.tick, up=False)
        else:
            remaining = target_200                          # v1: 예산 규칙 없음
        per_step = remaining / params.grid_steps
        cash_left = pf.cash + planned_sell_value
        for k in range(1, params.grid_steps + 1):
            price = round_tick(close * (1 - grid * k), params.tick, up=False)
            qty = int(per_step // price)
            if qty <= 0 or qty * price > cash_left:
                continue
            cash_left -= qty * price
            orders.append(Order(K200, "buy", "limit", qty, price, f"grid{k}"))

    # ── 레버리지 (상승장 & E>1, §7 — 2트랙)
    if f.f4_leverage and regime is Regime.BULL and w_lev > 0 and not force_liq:
        strat_target = w_lev * params.lev_strategic_ratio * usable
        strat_value = sum(l.qty * lev_close for l in lev_lots if l.kind == "lev_strat")
        diff = strat_target - strat_value
        if diff > params.band * equity:
            qty = int(diff / lev_close)
            if qty > 0:
                orders.append(Order(LEV, "buy", "market", qty, None, "lev_strat"))
        elif -diff > params.band * equity:
            qty = int(-diff / lev_close)
            if qty > 0:
                orders.append(Order(LEV, "sell", "market", qty, None, "lev_strat"))

        # 전술 트랙 — 레버리지 자체 시계열 EMA20/ATR20 (§5.1 예외)
        lev_ema, lev_atr = mlev.ema20[i], mlev.atr20[i]
        if lev_ema is not None and lev_atr is not None:
            tact_budget_each = w_lev * (1 - params.lev_strategic_ratio) * usable / 2
            has1 = any(l.kind == "lev_tact1" for l in lev_lots)
            has2 = any(l.kind == "lev_tact2" for l in lev_lots)
            if lev_close < lev_ema - params.lev_tact1_mult * lev_atr and not has1:
                qty = int(tact_budget_each / lev_close)
                if qty > 0:
                    orders.append(Order(LEV, "buy", "market", qty, None, "lev_tact1"))
            if lev_close < lev_ema - params.lev_tact2_mult * lev_atr and not has2:
                qty = int(tact_budget_each / lev_close)
                if qty > 0:
                    orders.append(Order(LEV, "buy", "market", qty, None, "lev_tact2"))
            if lev_close >= lev_ema and (has1 or has2):
                qty = sum(l.qty for l in lev_lots if l.kind in ("lev_tact1", "lev_tact2"))
                if qty > 0:
                    orders.append(Order(LEV, "sell", "market", qty, None, "lev_tact_exit"))

    return Plan(
        "OK", regime, e, w_200, w_lev, tuple(orders), gap_cancel_below,
        indicators={
            "close": close, "ma20": ma20, "ma60": ma60, "ma200": ma200,
            "ema20": m200.ema20[i], "atr20": atr, "grid": grid,
            "sigma20": sigma20, "sigma_down": m200.sigma_down[i], "sigma_ref": m200.sigma_ref[i],
            "equity": equity, "lev_close": lev_close,
        },
    )
