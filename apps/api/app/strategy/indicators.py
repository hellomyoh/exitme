"""지표 계산 — 전략 신호(정본 trade_algorithm_final.md §3)와 차트 교차 검증의 단일 수식.

plain-Python float64 루프로 구현한다 — TS(차트 표시용)와 알고리즘을 1:1로 맞춰
교차 검증(오차 < 1e-8)이 성립해야 한다 (feature-chart §12, ADR-005 예외 조항).

- SMA(n), EMA(n): pandas ewm(adjust=False)과 동일 — ema[0]=x[0], α=2/(n+1)
- ATR(n): Wilder 평활 — atr[n-1]=mean(tr[0..n-1]), atr[i]=(atr[i-1]*(n-1)+tr[i])/n
  (정본은 "ATR20"만 지정 — Wilder 표준을 채택, ASSUMPTIONS 기록)
- RSI(n): Wilder 평활 (차트 전용)
결과 리스트는 입력과 같은 길이이며 워밍업 구간은 None.
"""
from __future__ import annotations

import math


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    if not values:
        return []
    out: list[float | None] = [None] * len(values)
    alpha = 2.0 / (n + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def true_range(high: list[float], low: list[float], close: list[float]) -> list[float]:
    tr = [high[0] - low[0]] if high else []
    for i in range(1, len(high)):
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return tr


def atr(high: list[float], low: list[float], close: list[float], n: int = 20) -> list[float | None]:
    tr = true_range(high, low, close)
    out: list[float | None] = [None] * len(tr)
    if len(tr) < n:
        return out
    prev = sum(tr[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(tr)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def rsi(close: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(close)
    if len(close) <= n:
        return out
    gains, losses = [], []
    for i in range(1, len(close)):
        diff = close[i] - close[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)
    out[n] = _rsi(avg_g, avg_l)
    for i in range(n + 1, len(close)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = _rsi(avg_g, avg_l)
    return out


def rolling_vol_annualized(close: list[float], n: int = 20, downside_only: bool = False,
                           trading_days: int = 252) -> list[float | None]:
    """연율화 변동성 — σ20(표본 ddof=1) / σ_down(하락일만, 0 치환 아님 — min(r,0)² 평균).

    정본 §3: σ_down = √252 × √( mean( min(r_t, 0)² ) ), 20일 창.
    """
    rets: list[float] = [0.0]
    for i in range(1, len(close)):
        rets.append(close[i] / close[i - 1] - 1.0)
    out: list[float | None] = [None] * len(close)
    for i in range(n, len(close)):
        window = rets[i - n + 1 : i + 1]
        if downside_only:
            mean_sq = sum(min(r, 0.0) ** 2 for r in window) / n
            out[i] = math.sqrt(trading_days) * math.sqrt(mean_sq)
        else:
            mu = sum(window) / n
            var = sum((r - mu) ** 2 for r in window) / (n - 1)
            out[i] = math.sqrt(trading_days) * math.sqrt(var)
    return out
