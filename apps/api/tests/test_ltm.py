"""LTM 제품 엔진 테스트 (2026-09-06 채택) — 규칙·체결·결과 규약(BacktestResult)."""
from __future__ import annotations

from app.strategy.ltm import LTMParams, ltm_states, run_ltm_backtest, target_weights
from tests.test_strategy_backtest import make_bars


def _lev_from(bars: list[dict], mult: float) -> list[dict]:
    out, px, prev = [], 10_000.0, float(bars[0]["close"])
    for b in bars:
        c = float(b["close"])
        px *= 1 + mult * (c / prev - 1)
        prev = c
        out.append({**b, "open": px, "high": px * 1.005, "low": px * 0.995, "close": px})
    return out


def test_target_weights():
    assert target_weights(0.0, 2.0) == (0.0, 0.0)
    assert target_weights(1.0, 2.0) == (1.0, 0.0)
    assert target_weights(2.0, 2.0) == (0.0, 1.0)          # QLD 100%
    assert target_weights(2.0, 3.0) == (0.5, 0.5)          # QQQ 50% + TQQQ 50%
    assert target_weights(0.6, 2.0) == (0.6, 0.0)


def test_states_gate_momentum_and_shock():
    up = [100 * (1.001 ** i) for i in range(520)]
    st = ltm_states(up, LTMParams())
    assert st[251] is None and st[252] is not None                            # 워밍업 = max(MA200, 12M 252)
    assert st[460]["on"] and st[460]["e_target"] == 2.0                        # 추세 위·모멘텀 양·급락 없음 → 2배
    # 하루 −4% 급락 → 이후 20거래일 e_target 1.0, 그 뒤 복귀
    shock = up[:480] + [up[479] * 0.96] + [up[479] * 0.96 * (1.001 ** i) for i in range(1, 60)]
    st2 = ltm_states(shock, LTMParams())
    assert st2[481]["e_target"] == 1.0 and st2[481]["shock_left"] > 0
    assert st2[480 + 25]["e_target"] == 2.0
    # 12개월 수익률이 음수면 레버리지 금지(1배)
    down_then_up = [200 * (0.998 ** i) for i in range(300)] + [200 * (0.998 ** 299) * (1.003 ** i) for i in range(1, 260)]
    st3 = ltm_states(down_then_up, LTMParams())
    first_on = next(i for i, s in enumerate(st3) if s and s["on"])
    assert st3[first_on]["e_target"] == 1.0


def test_backtest_uptrend_holds_leverage_and_result_shape():
    b1 = make_bars(n=800, drift=0.002)
    r = run_ltm_backtest(b1, _lev_from(b1, 2.0), 1_000_000_00, LTMParams(lev_multiple=2.0))
    assert len(r.dates) == len(r.equity) == len(r.exposures) == len(r.regimes)
    assert r.kpi["total_return"] > 0 and "BULL" in r.regimes
    assert 1.8 <= r.exposures[-1] <= 2.2 and r.qty_lev[-1] > 0            # QLD 100% 근처
    assert any(f.kind == "ltm_entry" for f in r.fills) and any(f.kind in ("ltm_lever_on", "ltm_entry") for f in r.fills)
    assert r.final_lots and all(l["instrument"] in ("K200", "LEV") for l in r.final_lots)
    assert r.plans[-1].status == "OK" and "mom12" in r.plans[-1].indicators


def test_backtest_tqqq_splits_exposure_half_half():
    b1 = make_bars(n=800, drift=0.002)
    r = run_ltm_backtest(b1, _lev_from(b1, 3.0), 1_000_000_00, LTMParams(lev_multiple=3.0))
    v1 = r.qty_200[-1] * float(b1[-1]["close"])
    vL = r.qty_lev[-1] * float(_lev_from(b1, 3.0)[-1]["close"])
    assert vL > 0 and 0.3 < v1 / (v1 + vL) < 0.7                            # 50/50 근처(밴드 안 드리프트 허용)


def test_backtest_downtrend_goes_cash_and_seeding():
    b1 = make_bars(n=800, seed=11, drift=-0.002)
    r = run_ltm_backtest(b1, _lev_from(b1, 2.0), 1_000_000_00, LTMParams())
    assert r.regimes[-1] == "BEAR" and r.qty_200[-1] == 0 and r.qty_lev[-1] == 0
    # 보유 상태로 시작(실전 전환·보유 시작): 시드 로트가 원금에 합산되고 첫날부터 규칙 대상
    b2 = make_bars(n=800, drift=0.002)
    r2 = run_ltm_backtest(b2, _lev_from(b2, 2.0), 1_000_000_00, LTMParams(),
                          initial_lots=[{"leg": "K200", "qty": 100, "price": float(b2[0]["close"])}])
    assert r2.equity[0] > 1_000_000_00 * 0.9 and r2.kpi["total_return"] > 0
