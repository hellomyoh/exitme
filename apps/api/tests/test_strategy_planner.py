"""RAVG v2.5 플래너 테스트 — feature-strategy-engine.md §12 골든·경계 케이스.

Market 를 직접 구성해 지표값을 정밀 제어한다 (지표 계산 자체는 test_indicators.py 가 검증).
골든 수치 출처: G1·G2 는 SOURCES/basic_trade.md §5 예시(정본 v2 clip 0.8~4.0% 내부값으로 유효).
"""
import pytest

from app.strategy.params import AblationFlags, Params, round_tick
from app.strategy.planner import K200, LEV, Lot, Market, Portfolio, grid_ratio, plan
from app.strategy.regime import Regime, next_regime

P = Params()
N = 271
I = N - 1  # 워밍업 충족 최소 인덱스 (i+1 = 271 ≥ 270)


def mk_market(close=70000.0, ma20=71000.0, ma60=69000.0, ma200=65000.0, ema20=71000.0,
              atr=1400.0, sigma20=0.15, sigma_down=0.10, sigma_ref=0.10, n=N) -> Market:
    const = lambda v: [v] * n
    return Market(
        opens=const(close), highs=const(close * 1.01), lows=const(close * 0.99), closes=const(close),
        ma20=const(ma20), ma60=const(ma60), ma200=const(ma200), ema20=const(ema20),
        atr20=const(atr), sigma20=const(sigma20), sigma_down=const(sigma_down), sigma_ref=const(sigma_ref),
    )


def mk_lev(close=20000.0, ema20=21000.0, atr=500.0) -> Market:
    return mk_market(close=close, ema20=ema20, atr=atr)


def pf_with(cash: float, lots=None) -> Portfolio:
    return Portfolio(cash=cash, lots=lots or [])


# ── G1: 그리드 3단 지정가 (Close=70,000 · ATR/C=2% → Grid 1.5% → 68,950/67,900/66,850)
def test_golden_grid_prices():
    atr = 1400.0  # ATR/C = 2% → grid = clip(0.75×0.02, 0.008, 0.04) = 0.015
    assert grid_ratio(atr, 70000.0, P) == pytest.approx(0.015)
    prices = [round_tick(70000 * (1 - 0.015 * k), P.tick, up=False) for k in (1, 2, 3)]
    assert prices == [68950, 67900, 66850]


def test_golden_grid_orders_from_planner():
    m = mk_market()  # BULL: close>ma200, ma20>ma60
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(100_000_000), P)
    assert p.status == "OK" and p.regime is Regime.BULL
    grid_orders = [o for o in p.orders if o.kind.startswith("grid")]
    assert [o.price for o in grid_orders] == [68950, 67900, 66850]
    # 잔여예산 가중 50/30/20 (2026-09-03 채택) — 각 단계 금액이 가중비에 수렴 (오차 < 1주 가격)
    amounts = [o.price * o.qty for o in grid_orders]
    total = sum(amounts)
    for amt, w in zip(amounts, P.grid_weights):
        assert abs(amt - total * w) < 70000 * 2


def test_grid_weights_fallback_to_equal():
    """가중 길이 부족·합 0 이면 균등 폴백 (params 주석 계약)."""
    from dataclasses import replace as dc_replace
    m = mk_market()
    zero = dc_replace(P, grid_weights=(0.0, 0.0, 0.0))
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(100_000_000), zero)
    amounts = [o.price * o.qty for o in p.orders if o.kind.startswith("grid")]
    assert len(amounts) == 3 and max(amounts) - min(amounts) < 70000 * 2  # 균등 복원


# ── G2: 익절가 호가 올림 — 68,950 × 1.015 = 69,984.25 → 69,985
def test_golden_tp_price_rounding():
    assert round_tick(68950 * 1.015, P.tick, up=True) == 69985


def test_no_tp_orders_in_bull_but_tp_in_neutral():
    lot = Lot(K200, 100, 68950, "grid", 69985, 0)
    m_bull = mk_market()
    p_bull = plan(I, m_bull, mk_lev(), Regime.BULL, pf_with(1e8, [lot]), P)
    assert not [o for o in p_bull.orders if o.kind == "tp"]  # 상승장 익절 0건
    # 중립: close<ma200 진입 아님 — ma20<ma60 로 중립 유지 상태 구성
    m_neu = mk_market(ma20=68000.0, ma60=69000.0, ma200=65000.0)
    p_neu = plan(I, m_neu, mk_lev(), Regime.NEUTRAL, pf_with(1e8, [lot]), P)
    tp = [o for o in p_neu.orders if o.kind == "tp"]
    assert len(tp) == 1 and tp[0].price == 69985 and tp[0].qty == 100


# ── G3: 배분 3점 — 명세 §12 의 E=0.35/0.90/1.30 → (w_LEV, w_200, 현금) = (0,35,65)/(0,90,10)/(30,70,0)%
#    E_raw = 0.5×0.20/σd + 0.5×σref/σd (목표σ 0.20). 0.35 는 중립장(cap 0.65 미만), 0.90·1.30 은 상승장.
@pytest.mark.parametrize("bull,sd,sref,e_expect,wlev,w200", [
    (False, 0.60, 0.22, 0.35, 0.0, 0.35),   # 0.1667 + 0.1833
    (True, 0.30, 0.34, 0.90, 0.0, 0.90),    # 0.3333 + 0.5667
    (True, 0.10, 0.10, 1.30, 0.30, 0.70),   # E_raw 1.5 → 상승장 cap 1.30
])
def test_golden_allocation_formula(bull, sd, sref, e_expect, wlev, w200):
    # 중립: 종가 > MA200 이지만 MA20 < MA60 → 상승 진입 아님, 하락 진입 아님 → NEUTRAL 유지
    m = mk_market(sigma_down=sd, sigma_ref=sref) if bull else mk_market(ma20=68000.0, ma60=69000.0, sigma_down=sd, sigma_ref=sref)
    p = plan(I, m, mk_lev(), Regime.BULL if bull else Regime.NEUTRAL, pf_with(1e8), P)
    assert p.regime is (Regime.BULL if bull else Regime.NEUTRAL)
    assert p.e_target == pytest.approx(e_expect, abs=1e-9)
    assert (p.w_lev, p.w_200) == (pytest.approx(wlev, abs=1e-9), pytest.approx(w200, abs=1e-9))
    assert p.w_200 + 2 * p.w_lev == pytest.approx(p.e_target, abs=1e-9)   # 실효노출 항등식
    assert 1.0 - p.w_200 - p.w_lev == pytest.approx(1.0 - e_expect + (wlev if wlev else 0.0), abs=1e-9)  # 현금 = 1 − w_200 − w_LEV


def test_allocation_three_points():
    # σd·σref 를 조절해 E_raw 를 정확히 유도: E_raw = 0.5×0.20/sd + 0.5×sref/sd (목표σ 0.20, 2026-08-31)
    # sd=sref=s 이면 E_raw = 0.5×(0.20+s)/s
    cases = [
        (0.40, 0.75, 0.0, 0.75),       # E_raw = 0.5×0.60/0.40 = 0.75 → BULL emax 1.3 → E=0.75
        (0.20 / 1.3, 1.15, 0.15, 0.85),  # 0.5×(0.20+s)/s = 1.15 ⇔ s = 0.20/1.3
        (0.05, 1.30, 0.30, 0.70),      # E_raw = 2.5 → emax 1.30 캡
    ]
    for s, e, wlev, w200 in cases:
        m = mk_market(sigma_down=s, sigma_ref=s)
        p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), P)
        assert p.e_target == pytest.approx(e, abs=1e-9)
        assert p.w_lev == pytest.approx(wlev, abs=1e-9)
        assert p.w_200 == pytest.approx(w200, abs=1e-9)
        # 실효노출 = w_200 + 2×w_lev = E
        assert p.w_200 + 2 * p.w_lev == pytest.approx(p.e_target, abs=1e-9)


def test_e_one_boundary_leverage_zero_vs_positive():
    # E_raw = 1.0 정확히: 0.5×(0.20+s)/s = 1 → s = 0.20
    m = mk_market(sigma_down=0.20, sigma_ref=0.20)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), P)
    assert p.e_target == pytest.approx(1.0) and p.w_lev == 0.0
    m2 = mk_market(sigma_down=0.1999, sigma_ref=0.1999)
    p2 = plan(I, m2, mk_lev(), Regime.BULL, pf_with(1e8), P)
    assert p2.w_lev > 0.0


# ── 레짐 전이 (B1·B2·B3)
def test_regime_transitions_exhaustive():
    c = dict(close=70000.0, params=P)
    # 진입 AND
    assert next_regime(Regime.NEUTRAL, ma20=71000, ma60=69000, ma200=65000, **c) is Regime.BULL
    assert next_regime(Regime.NEUTRAL, ma20=68000, ma60=69000, ma200=75000, **c) is Regime.BEAR
    # 직행 (이탈+반대 진입 동시) — 양방향
    assert next_regime(Regime.BULL, ma20=68000, ma60=69000, ma200=75000, **c) is Regime.BEAR
    assert next_regime(Regime.BEAR, ma20=71000, ma60=69000, ma200=65000, **c) is Regime.BULL
    # 이탈 OR → 중립
    assert next_regime(Regime.BULL, ma20=71000, ma60=69000, ma200=75000, **c) is Regime.NEUTRAL  # close<ma200
    assert next_regime(Regime.BEAR, ma20=68000, ma60=69000, ma200=65000, **c) is Regime.NEUTRAL  # close>ma200


def test_regime_buffer_boundaries_strict():
    c = dict(close=70000.0, ma200=65000.0, params=P)
    # MA20 = MA60×0.98 정확히 → '<' 미충족 → BULL 유지
    assert next_regime(Regime.BULL, ma20=69000 * 0.98, ma60=69000, **c) is Regime.BULL
    # 살짝 아래 → 이탈
    assert next_regime(Regime.BULL, ma20=69000 * 0.98 - 1, ma60=69000, **c) is Regime.NEUTRAL
    # Close = MA200 정확히 → 진입·이탈 모두 미충족 → 중립 유지
    assert next_regime(Regime.NEUTRAL, close=65000.0, ma20=71000, ma60=69000, ma200=65000.0, params=P) is Regime.NEUTRAL
    # 데드존 왕복 무전이 (B3)
    r = Regime.BULL
    for ratio in (0.99, 0.985, 0.995, 0.99, 1.0):
        r = next_regime(r, close=70000.0, ma20=69000 * ratio, ma60=69000, ma200=65000.0, params=P)
    assert r is Regime.BULL


# ── 워밍업 (F1)
def test_warmup_guard():
    m = mk_market(n=N)
    p = plan(I - 1, m, mk_lev(), Regime.NEUTRAL, pf_with(1e8), P)  # i+1 = 270? I-1=269 → i+1=270 OK
    # 269일째(i=268)는 미충족
    p_short = plan(268, m, mk_lev(), Regime.NEUTRAL, pf_with(1e8), P)
    assert p_short.status == "INSUFFICIENT_HISTORY" and p_short.orders == () and p_short.e_target == 0.0
    assert p.status == "OK"


# ── σ_down floor (수치 안전)
def test_sigma_floor_keeps_e_finite():
    m = mk_market(sigma_down=0.0, sigma_ref=0.0)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), P)
    assert p.status == "OK"
    # floor 0.03: E_raw = 0.5×0.13/0.03 + 0.5×1 = 2.67 → emax 1.3 캡
    assert p.e_target == pytest.approx(1.30)


# ── 잔여예산 규칙 (B5): 보유 ≥ 목표 → 매수 0건
def test_budget_rule_no_orders_when_full():
    lots = [Lot(K200, 1300, 70000, "core", None, 0)]  # 9,100만 보유
    m = mk_market(sigma_down=0.26, sigma_ref=0.26)    # E=0.75 → target ≈ 0.75×usable
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(9_000_000, lots), P)
    assert not [o for o in p.orders if o.side == "buy" and o.instrument == K200]


# ── 리밸런싱 밴드 (B7): 5%p 이내 미실행 / 초과 실행
def test_band_reduce_only_beyond_5pp():
    m = mk_market(sigma_down=0.40, sigma_ref=0.40)  # E = 0.5×(0.20+0.40)/0.40 = 0.75 (목표σ 0.20)
    # equity 1억: target_200 = 0.75×1e8 = 75M
    lots_small_excess = [Lot(K200, 1120, 70000, "core", None, 0)]   # 78.4M — 초과 ~3.8%p
    p1 = plan(I, m, mk_lev(), Regime.BULL, pf_with(100_000_000 - 78_400_000, lots_small_excess), P)
    assert not [o for o in p1.orders if o.kind == "reduce"]
    lots_big_excess = [Lot(K200, 1220, 70000, "core", None, 0)]     # 85.4M — 초과 >10%p
    p2 = plan(I, m, mk_lev(), Regime.BEAR, pf_with(100_000_000 - 85_400_000, lots_big_excess), P)
    assert [o for o in p2.orders if o.kind == "reduce"]


# ── 하락장: 그리드 정지 + 레버리지 청산
def test_bear_stops_grid_and_liquidates_leverage():
    m = mk_market(ma20=68000.0, ma60=69000.0, ma200=75000.0)  # close<ma200, ma20<ma60 → BEAR
    lev_lot = Lot(LEV, 500, 20000, "lev_strat", None, 0)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(5e7, [lev_lot]), P)
    assert p.regime is Regime.BEAR
    assert not [o for o in p.orders if o.side == "buy"]
    liq = [o for o in p.orders if o.kind == "lev_liq"]
    assert len(liq) == 1 and liq[0].qty == 500


# ── σ20 > 35% 강제청산 (상승장이어도) — 2026-08-31 임계 상향
def test_sigma20_forces_leverage_liquidation():
    m = mk_market(sigma20=0.36)
    lev_lot = Lot(LEV, 300, 20000, "lev_tact1", None, 0)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(5e7, [lev_lot]), P)
    assert [o for o in p.orders if o.kind == "lev_liq"]


# ── 전술 트랙: 레버리지 자체 시계열 기준 + 1·2차 동시 충족 시 둘 다
def test_tactical_track_uses_lev_series_and_dual_entry():
    m = mk_market(sigma_down=0.05, sigma_ref=0.05)  # E=1.3 → w_lev=0.3
    # lev close=20000, ema=21000, atr=500 → 20000 < 21000-0.75×500=20625 (1차), < 21000-750=20250 (2차)
    p = plan(I, m, mk_lev(close=20000.0, ema20=21000.0, atr=500.0), Regime.BULL, pf_with(1e8), P)
    kinds = [o.kind for o in p.orders if o.instrument == LEV and o.side == "buy"]
    assert "lev_tact1" in kinds and "lev_tact2" in kinds
    # EMA 회귀 시 전술 이탈
    lot1 = Lot(LEV, 100, 20000, "lev_tact1", None, 0)
    p2 = plan(I, mk_market(sigma_down=0.05, sigma_ref=0.05), mk_lev(close=21500.0, ema20=21000.0),
              Regime.BULL, pf_with(1e8, [lot1]), P)
    assert [o for o in p2.orders if o.kind == "lev_tact_exit"]


# ── 절제 플래그 (A2 종속성)
def test_flag_f4_off_disables_leverage_entirely():
    params = Params(flags=AblationFlags(f4_leverage=False))
    m = mk_market(sigma_down=0.05, sigma_ref=0.05)
    p = plan(I, m, mk_lev(close=20000.0, ema20=21000.0, atr=500.0), Regime.BULL, pf_with(1e8), params)
    assert p.w_lev == 0.0 and p.e_target <= 1.0
    assert not [o for o in p.orders if o.instrument == LEV]


def test_flag_f2_off_uses_v1_total_vol():
    params = Params(flags=AblationFlags(f2_downside_vol=False))
    m = mk_market(sigma20=0.30, sigma_down=0.05, sigma_ref=0.05)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), params)
    assert p.e_target == pytest.approx(0.18 / 0.30)  # v1: 0.60


def test_flag_f1_off_tp_even_in_bull():
    params = Params(flags=AblationFlags(f1_no_tp_in_bull=False))
    lot = Lot(K200, 100, 68950, "grid", 69985, 0)
    p = plan(I, mk_market(), mk_lev(), Regime.BULL, pf_with(1e8, [lot]), params)
    assert [o for o in p.orders if o.kind == "tp"]


# ── 갭 필터 지시문 (조건부 지시문 §5.7)
def test_gap_cancel_threshold():
    m = mk_market(atr=1400.0)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), P)
    assert p.gap_cancel_below == round_tick(70000 - 1.5 * 1400, P.tick, up=False)  # 67,900


def test_sell_orders_never_exceed_holdings():
    """익절+축소 이중 계상 방지 (2026-08-28 사용자 검증): 매도 합계 ≤ 보유."""
    # 보유 680주(tp 부여) + 큰 초과 비중 → 축소와 익절이 같은 물량을 겹쳐 팔면 안 됨
    lot = Lot(K200, 680, 92000, "grid", 111540, 0)
    m = mk_market(ma20=68000.0, ma60=69000.0, ma200=65000.0, sigma_down=0.26, sigma_ref=0.26)  # NEUTRAL, E=0.75
    pf = pf_with(15_000_000, [lot])  # equity ≈ 62.6M, target ≈ 46.7M → excess > band
    p = plan(I, m, mk_lev(), Regime.NEUTRAL, pf, P)
    sells = [o for o in p.orders if o.instrument == K200 and o.side == "sell"]
    total_sell = sum(o.qty for o in sells)
    assert total_sell <= 680, f"매도 합계 {total_sell} > 보유 680"
    kinds = {o.kind for o in sells}
    if "reduce" in kinds and "tp" in kinds:
        # 축소분(FIFO 선점) 제외한 잔여만 익절
        reduce_q = next(o.qty for o in sells if o.kind == "reduce")
        tp_q = next(o.qty for o in sells if o.kind == "tp")
        assert reduce_q + tp_q <= 680


# ── 2026-08-28 공식 검증 소견 회귀 고정 (discussion/review-formulas 참조)
def test_leverage_liquidated_when_e_drops_below_one():
    """검증 ①① 치명: BULL 유지 중 E≤1.0 이 되면 레버리지 전량 매도 (정본 §5.2 '자동 0')."""
    m = mk_market(sigma_down=0.22, sigma_ref=0.22, sigma20=0.15)  # E<1 → w_lev=0 (목표σ 0.20 기준)
    lots = [Lot(LEV, 500, 20000, "lev_strat", None, 0), Lot(LEV, 100, 20000, "lev_tact1", None, 0)]
    p = plan(I, m, mk_lev(close=21500.0, ema20=21000.0), Regime.BULL, pf_with(5e7, lots), P)
    assert p.regime is Regime.BULL and p.w_lev == 0.0
    liq = [o for o in p.orders if o.instrument == LEV and o.side == "sell"]
    assert sum(o.qty for o in liq) == 600  # 전량


def test_tactical_exit_fires_even_when_wlev_zero_band():
    """검증 ①①: EMA20 회복 시 전술 이탈은 w_lev 값과 무관하게 발행."""
    m = mk_market(sigma_down=0.118, sigma_ref=0.118)  # E>1 → 레버리지 보유 상태에서 이탈 평가
    lots = [Lot(LEV, 100, 20000, "lev_tact1", None, 0)]
    p = plan(I, m, mk_lev(close=21500.0, ema20=21000.0), Regime.BULL, pf_with(1e8, lots), P)
    assert [o for o in p.orders if o.kind == "lev_tact_exit"]


def test_reduce_bypasses_band_on_regime_change_and_bear():
    """검증 ①② 치명: 레짐 전환·하락장은 밴드 무시 즉시 축소 (정본 §5.3·§6.2)."""
    # BULL→BEAR 전환일, 초과 4.1%p(밴드 5%p 미만)
    m = mk_market(ma20=68000.0, ma60=69000.0, ma200=75000.0, sigma_down=0.26, sigma_ref=0.26)
    lots = [Lot(K200, 343, 70000, "core", None, 0)]  # 24.0M
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(100_000_000 - 24_010_000, lots), P)
    assert p.regime is Regime.BEAR
    assert [o for o in p.orders if o.kind == "reduce"], "전환일 밴드 우회 축소 필요"
    # BEAR 정착 상태(전환 아님)에서도 초과분은 축소
    p2 = plan(I, m, mk_lev(), Regime.BEAR, pf_with(100_000_000 - 24_010_000, [Lot(K200, 343, 70000, "grid", 71000, 0)]), P)
    assert [o for o in p2.orders if o.kind == "reduce"]


def test_core_lot_gets_tp_on_transition_day():
    """검증 ①③: BULL→NEUTRAL 전환일에 core 로트도 전환일 종가 기준 익절 발행 (feature §5.6)."""
    m = mk_market(ma20=67000.0, ma60=69000.0, ma200=65000.0)  # ma20 < ma60×0.98 → NEUTRAL 전이
    lots = [Lot(K200, 300, 70000, "core", None, 0)]
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(8e7, lots), P)
    assert p.regime is Regime.NEUTRAL
    tp = [o for o in p.orders if o.kind == "tp"]
    assert tp and tp[0].qty == 300
    assert tp[0].price == round_tick(70000 * (1 + p.indicators["grid"]), P.tick, up=True)


def test_strategic_track_enters_at_small_wlev():
    """검증 ①④: 전략 트랙 신규 진입은 밴드 예외 — 소액 w_lev 에서도 전략:전술 분할 유지."""
    m = mk_market(sigma_down=0.118181, sigma_ref=0.118181)  # E>1 — 신규 진입 밴드 예외 확인
    p = plan(I, m, mk_lev(close=20000.0, ema20=21000.0, atr=500.0), Regime.BULL, pf_with(1e8), P)
    kinds = {o.kind for o in p.orders if o.instrument == LEV and o.side == "buy"}
    assert "lev_strat" in kinds, "신규 진입이 밴드에 막히면 안 됨"


def test_effective_exposure_targets_equal_e():
    """검증 D2: 목표는 equity 기준 — target_200/equity = w_200 정확 (버퍼 미침식)."""
    m = mk_market(sigma_down=0.05, sigma_ref=0.05)  # E=1.3, w_200=0.7
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), P)
    grid_amt = sum(o.qty * o.price for o in p.orders if o.kind.startswith("grid"))
    # 잔여예산 = 0.7×1e8 (버퍼 미차감) — 그리드 합이 65~70M 구간 (수량 floor 손실만)
    assert 65_000_000 < grid_amt <= 70_000_000


def test_gap_exact_threshold_boundary():
    """검증 D1·B3: 갭 판정은 정확 임계값 — 표시값 내림과 무관."""
    m = mk_market(close=99712.0, atr=780.006)
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(1e8), P)
    exact = 99712.0 - 1.5 * 780.006  # 98541.991
    assert p.gap_cancel_exact == pytest.approx(exact)
    assert p.gap_cancel_below == int(exact)
    # 경계: 98,541 ≤ exact → 발동 / 98,542 > exact → 미발동
    assert 98541 <= p.gap_cancel_exact and not (98542 <= p.gap_cancel_exact)



def test_ma200_exit_buffer_hysteresis():
    """2026-08-31 승인: MA200 이탈 다리 히스테리시스 ε=2% — 관통해야 이탈, 진입·직행은 무완충."""
    P2 = P
    # BULL 유지: 종가가 MA200 아래지만 −2% 이내
    assert next_regime(Regime.BULL, close=65000 * 0.99, ma20=71000, ma60=69000, ma200=65000.0, params=P2) is Regime.BULL
    # 정확히 −2% 경계 → '<' 미충족 → 유지
    assert next_regime(Regime.BULL, close=65000 * 0.98, ma20=71000, ma60=69000, ma200=65000.0, params=P2) is Regime.BULL
    # −2% 관통 → 이탈
    assert next_regime(Regime.BULL, close=65000 * 0.98 - 1, ma20=71000, ma60=69000, ma200=65000.0, params=P2) is Regime.NEUTRAL
    # BEAR 대칭: +2% 이내 반등은 유지, 관통 시 이탈
    assert next_regime(Regime.BEAR, close=65000 * 1.01, ma20=68000, ma60=69000, ma200=65000.0, params=P2) is Regime.BEAR
    assert next_regime(Regime.BEAR, close=65000 * 1.02 + 1, ma20=68000, ma60=69000, ma200=65000.0, params=P2) is Regime.NEUTRAL
    # 급락 직행(BEAR 진입)은 무완충 — MA200 1원 아래 + MA20<MA60 이면 즉시 BEAR
    assert next_regime(Regime.BULL, close=64999.0, ma20=68000, ma60=69000, ma200=65000.0, params=P2) is Regime.BEAR


def test_grid_cap_is_2_5_percent():
    """그리드 간격 상한 (2026-09-12 사용자 지시 4.0% → 2.5%, docs/grid-cap-study-20260912.md).

    상한은 고변동일에만 물린다 — 평시(중앙 1.08%)에는 계산값 그대로다.
    """
    assert P.grid_max == 0.025
    # 계산값이 상한 위 — 2026년형 고변동일 (0.75 × ATR/C = 3.85%)
    assert grid_ratio(0.0513 * 70000, 70000.0, P) == pytest.approx(0.025)
    # 계산값이 상한 아래 — 평시는 클립되지 않는다
    assert grid_ratio(0.0144 * 70000, 70000.0, P) == pytest.approx(0.0108)
    # 하한은 그대로 0.8%
    assert grid_ratio(0.001 * 70000, 70000.0, P) == pytest.approx(0.008)


def test_grid_cap_moves_ladder_up_on_volatile_days():
    """상한 인하의 효과는 1~3차 지정가가 종가 쪽으로 올라오는 것뿐 — 단계 간격 비율은 그대로."""
    from dataclasses import replace

    atr = 0.06 * 70000            # 계산값 4.5% → 두 상한 모두 바인딩 (0.75 × 6% )
    g_new = grid_ratio(atr, 70000.0, P)
    g_old = grid_ratio(atr, 70000.0, replace(P, grid_max=0.04))
    assert (g_new, g_old) == (0.025, 0.04)
    assert [round(70000 * (1 - g_new * k)) for k in (1, 2, 3)] == [68250, 66500, 64750]
    assert [round(70000 * (1 - g_old * k)) for k in (1, 2, 3)] == [67200, 64400, 61600]


def test_leverage_buys_respect_the_shared_cash_buffer():
    """감사 A8 (2026-09-12): 현금 1억·E=1.30·전술 1·2차 동시 — 종전엔 K200 그리드 + 레버리지 매수 명목이 99,910,400원으로
    버퍼 반영 가용액 99,500,000원을 410,400원 넘었다. 두 종목이 한 지갑(cash_left)을 쓰면 넘지 않는다."""
    m = mk_market()                                          # BULL, E_raw 1.5 → cap 1.30
    lev = mk_lev(close=20000.0, ema20=21000.0, atr=500.0)    # 20000 < 21000 − 750 → 전술 1·2차 동시
    p = plan(I, m, lev, Regime.BULL, pf_with(100_000_000), P)
    assert p.e_target == pytest.approx(1.30)
    buys = [o for o in p.orders if o.side == "buy"]
    total = sum((o.price or 20000) * o.qty for o in buys)
    assert total <= 100_000_000 * (1 - P.cash_buffer)
    # 그리드(먼저 계산) 는 그대로 — 줄어드는 건 뒤에 오는 레버리지 매수만
    assert [o.price for o in buys if o.kind.startswith("grid")] == [68950, 67900, 66850]
    lev_buys = {o.kind: o.qty for o in buys if o.instrument == LEV}
    assert set(lev_buys) == {"lev_strat", "lev_tact1", "lev_tact2"} and all(q > 0 for q in lev_buys.values())
    # 현금이 얇으면 레버리지 매수도 가용 현금 안으로 줄고, 다 쓰면 다음 줄은 나오지 않는다 (종전엔 현금과 무관하게 수량이 나왔다).
    # K200 은 목표 이하(축소 매도 없음), 전략 트랙은 초과(매도 — 그 대금은 매수 재원으로 세지 않는다: 보수적)
    lots = [Lot(K200, 700, 70000, "core", None, 0), Lot(LEV, 1050, 20000, "lev_strat", None, 0)]
    p1 = plan(I, m, lev, Regime.BULL, pf_with(1_000_000, lots), P)
    eq = 700 * 70000 + 1050 * 20000 + 1_000_000
    avail = 1_000_000 - eq * P.cash_buffer
    assert not [o for o in p1.orders if o.kind == "reduce"]
    all_buys = sum((o.price or 20000) * o.qty for o in p1.orders if o.side == "buy")
    lev_buys_total = sum(20000 * o.qty for o in p1.orders if o.instrument == LEV and o.side == "buy")
    assert 0 < lev_buys_total and all_buys <= avail


def test_total_leverage_cap_reduces_tactical_excess_when_e_falls():
    """ADR-012 (감사 A3 반례): 총자산 1억, K200 8,400만, 레버 전략 700만·전술 450만+450만, E 1.30→1.10.
    종전엔 목표 10% vs 보유 16% 인데 전략 트랙만 보느라 주문 0 — 이제 초과분(6%p > 밴드 5%p)을 목표까지 축소한다."""
    m = mk_market(sigma_down=0.15, sigma_ref=0.13, sigma20=0.2)          # E = 0.5×0.2/0.15 + 0.5×0.13/0.15 = 1.10
    lots = [Lot(K200, 1200, 70000, "core", None, 0), Lot(LEV, 350, 20000, "lev_strat", None, 0),
            Lot(LEV, 225, 20000, "lev_tact1", None, 0), Lot(LEV, 225, 20000, "lev_tact2", None, 0)]
    p = plan(I, m, mk_lev(), Regime.BULL, pf_with(0, lots), P)             # 레버 종가 20000 < EMA 21000 → 전술 이탈 아님
    assert p.e_target == pytest.approx(1.10) and p.w_lev == pytest.approx(0.10)
    cap = [o for o in p.orders if o.kind == "lev_cap"]
    assert len(cap) == 1 and cap[0].side == "sell" and cap[0].otype == "market"
    # 초과 6,000,000원 ÷ 20,000 = 300주 → 남는 보유 ≈ 500주 = 목표 1,000만원. E=1.10 이 부동소수라 floor 가 299 로 떨어질 수 있다
    # (K200 축소 `int(excess/close)` 와 같은 내림 규약) — 1주 차이는 허용
    assert abs(cap[0].qty - 300) <= 1
    # 초과가 밴드 안이면 그대로 둔다 (E 1.30, 보유 30% = 목표)
    m2 = mk_market()                                                        # E cap 1.30 → w_lev 0.30 → 목표 3,000만
    lots2 = [Lot(K200, 1000, 70000, "core", None, 0), Lot(LEV, 1050, 20000, "lev_strat", None, 0),
             Lot(LEV, 225, 20000, "lev_tact1", None, 0), Lot(LEV, 225, 20000, "lev_tact2", None, 0)]
    p2 = plan(I, m2, mk_lev(), Regime.BULL, pf_with(0, lots2), P)
    assert not [o for o in p2.orders if o.kind == "lev_cap"]


def test_total_leverage_cap_counts_same_day_tactical_exit_and_strategic_sell():
    """같은 날 계획된 전술 이탈·전략 매도를 먼저 반영한 뒤 남는 초과만 축소한다 — 매도 중복 금지 (QA A3)."""
    m = mk_market(sigma_down=0.15, sigma_ref=0.13, sigma20=0.2)          # E 1.10 → 목표 레버 10%
    lots = [Lot(K200, 1200, 70000, "core", None, 0), Lot(LEV, 350, 20000, "lev_strat", None, 0),
            Lot(LEV, 225, 20000, "lev_tact1", None, 0), Lot(LEV, 225, 20000, "lev_tact2", None, 0)]
    # 레버 종가가 EMA 위 → 전술 450주 이탈 매도가 먼저 계획됨 → 남는 전략 350주 = 700만 = 목표 → 상한 축소 없음
    p = plan(I, m, mk_lev(close=21500.0, ema20=21000.0), Regime.BULL, pf_with(0, lots), P)
    kinds = [o.kind for o in p.orders if o.instrument == LEV and o.side == "sell"]
    assert "lev_tact_exit" in kinds and "lev_cap" not in kinds
    assert sum(o.qty for o in p.orders if o.instrument == LEV and o.side == "sell") == 450

