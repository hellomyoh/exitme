"""LTV/LTM 엔진 단위 테스트 (2026-09-06) — 규칙이 코드에 그대로 박혔는지 합성 시계열로 확인."""
from __future__ import annotations

import math

from app.strategy.ltv import LTVParams, buy_and_hold, drawdown_episodes, run_ltv


def _bars(closes: list[float], start="2020-01-01") -> list[dict]:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    out = []
    i = 0
    d = d0
    while len(out) < len(closes):
        if d.weekday() < 5:
            c = closes[len(out)]
            out.append({"date": d.isoformat(), "open": c, "close": c})
        d += timedelta(days=1)
    return out


def _lev_from(bars_1x: list[dict], mult: float) -> list[dict]:
    """1배 시계열에서 일일 배수 L 인 레버리지 시계열 합성."""
    out = []
    px = 100.0
    prev = float(bars_1x[0]["close"])
    for b in bars_1x:
        c = float(b["close"])
        px *= 1 + mult * (c / prev - 1)
        prev = c
        out.append({"date": b["date"], "open": px, "close": px})
    return out


def test_trend_gate_hysteresis_and_cash_when_off():
    # 300일 완만 상승 후 급락 → MA200 아래 2% 이탈 시 전량 현금, 다시 위로 가면 재진입
    up = [100 * (1.001 ** i) for i in range(300)]
    down = [up[-1] * (0.985 ** i) for i in range(1, 40)]
    rec = [down[-1] * (1.02 ** i) for i in range(1, 80)]
    b1 = _bars(up + down + rec)
    b2 = _lev_from(b1, 2.0)
    r = run_ltv(b1, b2, 1_000_000, LTVParams(sigma_target=None, e_max=1.0))
    on = r.regime_on
    assert any(on) and not all(on)
    # 이탈 후 노출 0, 재진입 후 노출 1
    off_idx = next(i for i in range(200, len(on)) if not on[i])
    assert r.exposure[off_idx + 2] < 0.05
    assert r.flips >= 2 and r.exposure[-1] > 0.9


def test_leverage_weights_reach_target_exposure():
    # 상승장에서 고정 2x: 실효 노출 ≈ 2.0 (QLD 100%), 3배 레그면 QQQ 50% + TQQQ 50%
    up = [100 * (1.0008 ** i) for i in range(320)]
    b1 = _bars(up)
    for mult in (2.0, 3.0):
        r = run_ltv(b1, _lev_from(b1, mult), 1_000_000, LTVParams(sigma_target=None, e_max=2.0, lev_multiple=mult))
        # 목표 2.0 — 리밸런스 밴드(10%) 안에서 레버리지 레그가 더 빨리 올라 2.0~2.2 로 드리프트할 수 있다
        assert 1.85 <= r.exposure[-1] <= 2.2, (mult, r.exposure[-1])
    # 레버리지 레그가 없으면 1배까지만
    r1 = run_ltv(b1, None, 1_000_000, LTVParams(sigma_target=None, e_max=2.0))
    assert abs(r1.exposure[-1] - 1.0) < 0.02


def test_shock_breaker_blocks_leverage_for_n_days():
    up = [100 * (1.0008 ** i) for i in range(260)]
    shock = up[-1] * 0.96                      # 하루 −4%
    after = [shock * (1.001 ** i) for i in range(1, 60)]
    b1 = _bars(up + [shock] + after)
    b2 = _lev_from(b1, 2.0)
    p = LTVParams(sigma_target=None, e_max=2.0, shock_drop=0.03, shock_days=20)
    r = run_ltv(b1, b2, 1_000_000, p)
    i_shock = 260
    # 쇼크 다음 리밸런스부터 1배로 내려오고, 20거래일 뒤 다시 2배로 복귀
    assert r.exposure[i_shock + 2] < 1.15
    assert r.exposure[-1] > 1.8
    r0 = run_ltv(b1, b2, 1_000_000, LTVParams(sigma_target=None, e_max=2.0))
    assert r0.exposure[i_shock + 2] > 1.8   # 브레이커 없으면 그대로 2배


def test_momentum_filter_allows_leverage_only_when_positive():
    # 12M 수익률이 음수인 상승 전환 초기: MA200 위지만 모멘텀 ≤ 0 → 1배, 모멘텀 양전환 후 2배
    down = [200 * (0.998 ** i) for i in range(260)]
    flat_up = [down[-1] * (1.0015 ** i) for i in range(1, 400)]
    b1 = _bars(down + flat_up)
    b2 = _lev_from(b1, 2.0)
    r = run_ltv(b1, b2, 1_000_000, LTVParams(sigma_target=None, e_max=2.0, mom_filter=252))
    on_idx = [i for i, on in enumerate(r.regime_on) if on]
    first_on = on_idx[0]
    # 상승 판정 직후 구간: 12M 수익률 음수 → 노출 ≤ 1.05
    assert max(r.exposure[first_on + 2:first_on + 40]) < 1.05
    assert r.exposure[-1] > 1.8


def test_volatility_target_scales_exposure():
    import random

    random.seed(7)
    calm = [100.0]
    for _ in range(300):
        calm.append(calm[-1] * (1 + random.gauss(0.0006, 0.004)))   # 연 σ≈6%
    wild = [calm[-1]]
    for _ in range(120):
        wild.append(wild[-1] * (1 + random.gauss(0.001, 0.03)))      # 연 σ≈48%
    b1 = _bars(calm + wild[1:])
    b2 = _lev_from(b1, 2.0)
    r = run_ltv(b1, b2, 1_000_000, LTVParams(sigma_target=0.25, e_floor=1.0, e_max=2.0))
    calm_e = r.exposure[290]
    assert calm_e > 1.8            # 잔잔하면 상한 2.0
    # 고변동 구간 중 상승 판정이 유지된 날들의 노출은 하한 1.0 근처로 내려온다
    wild_on = [e for e, on in zip(r.exposure[330:], r.regime_on[330:]) if on]
    assert wild_on and min(wild_on) < 1.2


def test_drawdown_episodes_and_buy_and_hold():
    eq = [100, 110, 99, 105, 120, 90, 95, 130]
    dates = [f"2020-01-{i + 1:02d}" for i in range(len(eq))]
    eps = drawdown_episodes(dates, eq, top=2)
    assert math.isclose(eps[0]["depth"], 90 / 120 - 1) and eps[0]["peak"] == "2020-01-05" and eps[0]["recovered"] == "2020-01-08"
    assert math.isclose(eps[1]["depth"], 99 / 110 - 1)
    b = _bars([100, 101, 102, 103, 104])
    bh = buy_and_hold(b, 1000.0, fee_annual=0.0)
    assert abs(bh.kpi["total_return"] - (104 / 101 - 1)) < 1e-9   # 첫날 시가(=101) 진입
