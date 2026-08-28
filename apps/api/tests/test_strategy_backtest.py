"""백테스트 시뮬레이터 테스트 — feature-backtest.md §12 (체결·비용·KPI·look-ahead·재현성·절제)."""
from datetime import date, timedelta

import pytest

from app.strategy.backtest import _fill_limit_buy, _fill_limit_sell, compute_kpi, run_backtest
from app.strategy.params import AblationFlags, Params

P = Params()


# ── D1/D3: 지정가 체결 판정식
def test_limit_buy_fill_rules():
    assert _fill_limit_buy(68950, open_px=68000, low=67000) == 68000   # Open ≤ L → Open (갭 유리)
    assert _fill_limit_buy(68950, open_px=70000, low=68950) == 68950.0  # Low ≤ L < Open → L (동가 포함)
    assert _fill_limit_buy(68950, open_px=70000, low=69000) is None     # Low > L → 미체결


def test_limit_sell_fill_rules():
    assert _fill_limit_sell(69985, open_px=71000, high=71500) == 71000  # Open ≥ S → Open
    assert _fill_limit_sell(69985, open_px=69000, high=69985) == 69985.0
    assert _fill_limit_sell(69985, open_px=69000, high=69500) is None


# ── KPI 수기 대조 (K1·K2)
def test_kpi_mdd_hand_calc():
    kpi = compute_kpi([100.0, 120.0, 90.0, 130.0, 80.0], 100.0, [])
    assert kpi["mdd"] == pytest.approx(80 / 130 - 1, abs=1e-9)  # −38.46%
    assert kpi["total_return"] == pytest.approx(-0.2)
    assert kpi["cagr"] is None  # 1년 미만 → 누적수익률만 (§5.3)


def test_kpi_cagr_252():
    equity = [100.0 * (1.001 ** (i + 1)) for i in range(252)]
    kpi = compute_kpi(equity, 100.0, [])
    assert kpi["cagr"] == pytest.approx(1.001 ** 252 - 1, rel=1e-9)


# ── 합성 시계열 생성 (결정론 LCG)
def make_bars(n=700, seed=7, start_price=70000.0, drift=0.0004, ratio=1.0):
    state = seed
    def rnd():
        nonlocal state
        state = (state * 1103515245 + 12345) % (2 ** 31)
        return state / (2 ** 31)
    bars = []
    price = start_price * ratio
    d = date(2020, 1, 2)
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        r = (rnd() - 0.5) * 0.03 + drift * (2 if ratio != 1.0 else 1)
        o = price
        c = max(price * (1 + r), 1000)
        h = max(o, c) * (1 + rnd() * 0.008)
        l = min(o, c) * (1 - rnd() * 0.008)
        bars.append({"date": d.isoformat(), "open": round(o), "high": round(h),
                     "low": round(l), "close": round(c), "volume": 1000})
        price = c
        d += timedelta(days=1)
    return bars


@pytest.fixture(scope="module")
def bars():
    return make_bars(), make_bars(ratio=0.3)  # (K200, LEV — 레버리지는 스케일·드리프트 상이)


# ── 재현성 (R1): 동일 입력 2회 → 동일 결과
def test_reproducible(bars):
    b200, blev = bars
    r1 = run_backtest(b200, blev, 100_000_000, P)
    r2 = run_backtest(b200, blev, 100_000_000, P)
    assert r1.equity == r2.equity
    assert [t.pnl for t in r1.trades] == [t.pnl for t in r2.trades]


# ── look-ahead (C1): 절단 재실행 시 앞부분 계획 동일
def test_no_lookahead_masking(bars):
    b200, blev = bars
    full = run_backtest(b200, blev, 100_000_000, P, collect_plans=True)
    cut = 400
    part = run_backtest(b200[:cut], blev[:cut], 100_000_000, P, collect_plans=True)
    # 부분 실행의 마지막 계획일까지 전 구간 계획 동일 (지표 인과성 + 상태 체인 결정론)
    for i, (pf, pp) in enumerate(zip(full.plans[: len(part.plans)], part.plans)):
        assert pf.regime == pp.regime, f"regime differs at {i}"
        assert pf.orders == pp.orders, f"orders differ at {i}"
        assert pf.e_target == pp.e_target, f"E differs at {i}"


# ── 비용 단조성 (E1): 비용 반영 시 수익 감소
def test_cost_monotonicity(bars):
    b200, blev = bars
    no_cost = Params(commission=0.0, slippage_market=0.0, lev_tax=0.0, fee_200=0.0, fee_lev=0.0)
    with_cost = P
    r_free = run_backtest(b200, blev, 100_000_000, no_cost)
    r_cost = run_backtest(b200, blev, 100_000_000, with_cost)
    assert r_cost.equity[-1] < r_free.equity[-1]


# ── 절제 f4 (A2): 레버리지 off → LEV 거래 0건, 실효노출 ≤ 1
def test_ablation_f4_off_no_lev_trades(bars):
    b200, blev = bars
    params = Params(flags=AblationFlags(f4_leverage=False))
    r = run_backtest(b200, blev, 100_000_000, params)
    assert not [t for t in r.trades if t.instrument == "LEV"]
    assert max(r.exposures) <= 1.0 + 1e-9


# ── 절제 5플래그 전 ON = 기본 프리셋과 일치 (A1)
def test_ablation_all_on_equals_default(bars):
    b200, blev = bars
    r1 = run_backtest(b200, blev, 100_000_000, P)
    r2 = run_backtest(b200, blev, 100_000_000, Params(flags=AblationFlags(True, True, True, True, True)))
    assert r1.equity == r2.equity


# ── v1 레짐(f3 off) 경로 동작
def test_ablation_f3_off_runs(bars):
    b200, blev = bars
    r = run_backtest(b200, blev, 100_000_000, Params(flags=AblationFlags(f3_fast_regime=False)))
    assert len(r.equity) > 0 and r.equity[-1] > 0


# ── 구조 불변식: 하락 레짐 구간에서 신규 매수 없음 → K200 노출이 목표 이하로 수렴
def test_bear_regime_reduces_exposure():
    # 강한 하락 추세 합성: drift 음수
    b200 = make_bars(n=700, seed=11, drift=-0.002)
    blev = make_bars(n=700, seed=11, drift=-0.004, ratio=0.3)
    r = run_backtest(b200, blev, 100_000_000, P)
    bear_days = [i for i, s in enumerate(r.regimes) if s == "BEAR"]
    assert bear_days, "합성 하락장에서 BEAR 레짐이 발생해야 함"
    # 하락장 막바지 노출이 Emax_bear 이하
    last_bear = bear_days[-1]
    assert r.exposures[last_bear] <= P.emax_bear + 1e-9


# ── 벤치마크: 매수보유 곡선 존재·양수
def test_benchmark_curve(bars):
    b200, blev = bars
    r = run_backtest(b200, blev, 100_000_000, P)
    assert len(r.benchmark) == len(r.equity)
    assert all(v > 0 for v in r.benchmark)


# ── 거래 원장: FIFO 라운드트립 무결성 (수량 보존)
def test_trade_ledger_quantity_conservation(bars):
    b200, blev = bars
    r = run_backtest(b200, blev, 100_000_000, P)
    assert r.kpi["trades"] == len(r.trades)
    for t in r.trades:
        assert t.qty > 0 and t.sell_index > t.buy_index
