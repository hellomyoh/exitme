"""레짐 3-상태 머신 — feature-strategy-engine.md §5.4 확정 규칙.

평가 순서 고정: ① Bear 진입 ② Bull 진입 ③ 현재 상태의 이탈 → Neutral.
경계는 엄격 부등호(등호 = 미충족). 초기 상태 Neutral.
v1 판정(f3 off): Close>MA200 AND MA60_t > MA60_{t-20} (이탈은 AND 부정).
"""
from __future__ import annotations

from enum import Enum

from app.strategy.params import Params


class Regime(str, Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"


def next_regime(
    prev: Regime,
    close: float,
    ma20: float,
    ma60: float,
    ma200: float,
    params: Params,
    ma60_prev_slope: float | None = None,  # v1 판정용: MA60_{t-lookback}
) -> Regime:
    if not params.flags.f3_fast_regime:
        # v1: 기울기 판정 (절제 비교용)
        if ma60_prev_slope is None:
            return Regime.NEUTRAL
        if close > ma200 and ma60 > ma60_prev_slope:
            return Regime.BULL
        if close < ma200 and ma60 < ma60_prev_slope:
            return Regime.BEAR
        return Regime.NEUTRAL

    buf = params.regime_buffer
    bear_entry = close < ma200 and ma20 < ma60
    bull_entry = close > ma200 and ma20 > ma60

    # ① Bear 진입 (이탈+반대편 진입 동시 → 직행)
    if bear_entry:
        return Regime.BEAR
    # ② Bull 진입
    if bull_entry:
        return Regime.BULL
    # ③ 현재 상태의 이탈 → Neutral — 두 다리 모두 히스테리시스:
    #    MA20/MA60 다리는 buf(2%), MA200 다리는 ε(ma200_exit_buffer, 2026-08-31 승인).
    #    이탈의 95% 가 MA200 다리에서 발생하는데 완충이 없어 마이크로 전환을 유발했음
    #    (docs/regime-buffer-study-20260831.md — KR·US 3중 검증). 급락 직행(①)은 무완충 유지.
    eps = params.ma200_exit_buffer
    if prev is Regime.BULL:
        if close < ma200 * (1 - eps) or ma20 < ma60 * (1 - buf):
            return Regime.NEUTRAL
        return Regime.BULL
    if prev is Regime.BEAR:
        if close > ma200 * (1 + eps) or ma20 > ma60 * (1 + buf):
            return Regime.NEUTRAL
        return Regime.BEAR
    return Regime.NEUTRAL
