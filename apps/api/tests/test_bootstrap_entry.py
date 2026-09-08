"""소량 진입 부트스트랩 (ADR-010, 2026-09-08) — 플래너 규칙·엔진 카운트·끔 = 종전과 동일. DB 불필요."""
from __future__ import annotations

from dataclasses import replace

from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, Lot, plan
from app.strategy.regime import Regime
from tests.test_strategy_planner import I, P, mk_lev, mk_market, pf_with


def _boot(p):
    return [o for o in p.orders if o.kind == "boot"]


def _grid(p):
    return [o for o in p.orders if o.kind.startswith("grid")]


def test_bootstrap_only_inside_window_and_off_when_none():
    """days_since_start None → 종전 계획 그대로. 0~9 → 부트스트랩 1줄, 10 이상 → 없음."""
    m = mk_market()
    base = plan(I, m, mk_lev(), Regime.NEUTRAL, pf_with(100_000_000), P)
    assert not _boot(base)
    for d in (0, 5, 9):
        p = plan(I, m, mk_lev(), Regime.NEUTRAL, pf_with(100_000_000), P, days_since_start=d)
        assert len(_boot(p)) == 1, d
        assert p.indicators["boot_day"] == d + 1 and p.indicators["boot_days"] == 10
    for d in (10, 11, 250):
        p = plan(I, m, mk_lev(), Regime.NEUTRAL, pf_with(100_000_000), P, days_since_start=d)
        assert not _boot(p) and p.orders == base.orders, d
    # boot_frac 0 또는 boot_days 0 = 끔
    for params in (replace(P, boot_frac=0.0), replace(P, boot_days=0)):
        p = plan(I, m, mk_lev(), Regime.NEUTRAL, pf_with(100_000_000), params, days_since_start=0)
        assert not _boot(p) and p.orders == base.orders


def test_bootstrap_size_price_and_grid_scaling():
    """부트스트랩 = 미달분 × f 를 종가 지정가(δ 0)로, 그리드는 (1−f) 예산. 첫 주문 순서: boot → grid1 → grid2 → grid3."""
    m = mk_market()
    pf = pf_with(100_000_000)
    base = plan(I, m, mk_lev(), Regime.NEUTRAL, pf, P)
    p = plan(I, m, mk_lev(), Regime.NEUTRAL, pf, P, days_since_start=0)
    b = _boot(p)[0]
    close = m.closes[I]
    equity = pf.equity(close, mk_lev().closes[I])
    target = p.w_200 * equity
    assert b.side == "buy" and b.otype == "limit" and b.instrument == K200
    assert b.price == round_tick(close * (1 - P.boot_delta * p.indicators["grid"]), P.tick, up=False) == round_tick(close, P.tick, up=False)
    assert b.qty == int(P.boot_frac * target // b.price)
    # 그리드 수량은 (1−f) 배 (정수 내림이라 ±1)
    for g0, g1 in zip(_grid(base), _grid(p)):
        assert g0.kind == g1.kind and g0.price == g1.price
        assert abs(g1.qty - int(g0.qty * (1 - P.boot_frac))) <= 1
    assert [o.kind for o in p.orders if o.side == "buy" and o.instrument == K200] == ["boot", "grid1", "grid2", "grid3"]
    # 갭 취소 기준은 종전과 동일
    assert p.gap_cancel_exact == base.gap_cancel_exact


def test_bootstrap_bear_uses_multiplier_and_sets_gap_filter():
    """하락장: 그리드는 정지, 부트스트랩만 f × bear_mult(기본 0.5) — 갭 필터 기준도 붙는다. bear_mult 0 이면 하락장 진입 없음."""
    m = mk_market(close=60000.0, ma20=70000.0, ma60=72000.0, ma200=75000.0)   # MA200 아래 → BEAR 유지
    pf = pf_with(100_000_000)
    base = plan(I, m, mk_lev(), Regime.BEAR, pf, P)
    assert base.regime is Regime.BEAR and not _grid(base) and base.gap_cancel_exact is None
    p = plan(I, m, mk_lev(), Regime.BEAR, pf, P, days_since_start=3)
    assert p.regime is Regime.BEAR and not _grid(p)
    b = _boot(p)[0]
    target = p.w_200 * pf.equity(m.closes[I], mk_lev().closes[I])
    assert b.qty == int(P.boot_frac * P.boot_bear_mult * target // b.price) and b.qty > 0
    assert p.gap_cancel_exact is not None and p.gap_cancel_below == int(p.gap_cancel_exact)
    p0 = plan(I, m, mk_lev(), Regime.BEAR, pf, replace(P, boot_bear_mult=0.0), days_since_start=3)
    assert not _boot(p0) and p0.gap_cancel_exact is None


def test_bootstrap_stops_at_target_and_respects_cash():
    """목표에 닿으면(미달분 0) 부트스트랩 없음. 현금이 모자라면 내지 않고 그리드 예산은 그대로."""
    m = mk_market()
    close = m.closes[I]
    # 목표를 넘는 보유 → 미달분 0
    lots = [Lot(K200, 2000, int(close), "grid", None, 0)]
    p = plan(I, m, mk_lev(), Regime.NEUTRAL, pf_with(1_000_000, lots), P, days_since_start=0)
    assert not _boot(p)
    # 현금이 한 주 값도 안 되면 부트스트랩 없음, 계획은 종전과 같다
    tiny = pf_with(1_000)
    base = plan(I, m, mk_lev(), Regime.NEUTRAL, tiny, P)
    p2 = plan(I, m, mk_lev(), Regime.NEUTRAL, tiny, P, days_since_start=0)
    assert not _boot(p2) and p2.orders == base.orders


def test_engine_counts_days_from_first_ok_plan_and_off_equals_previous():
    """엔진: 첫 OK 계획일이 0일째 → 10일간 boot 줄, 11일째부터 없음. boot 끄면 종전 엔진과 완전히 같은 결과."""
    from app.strategy.backtest import run_backtest
    from tests.test_strategy_backtest import make_bars

    b200, blev = make_bars(n=600), make_bars(n=600, ratio=0.3)
    on = run_backtest(b200, blev, 100_000_000, Params(), collect_plans=True)
    off = run_backtest(b200, blev, 100_000_000, replace(Params(), boot_frac=0.0), collect_plans=True)
    ok_idx = [i for i, p in enumerate(on.plans) if p.status == "OK"]
    first = ok_idx[0]
    boots = [i for i, p in enumerate(on.plans) if any(o.kind == "boot" for o in p.orders)]
    assert boots and min(boots) == first and max(boots) <= first + 9
    assert all(i - first < 10 for i in boots)
    assert not any(o.kind == "boot" for p in off.plans for o in p.orders)
    # 끔 = 종전 규칙 (부트스트랩 도입 전 엔진과 같은 경로) — 하락장 시작이면 주문이 없을 수 있으니 계획 목록 자체를 비교
    assert [p.orders for p in off.plans[:first]] == [p.orders for p in on.plans[:first]]
    assert len(on.equity) == len(off.equity)


def test_engine_no_bootstrap_when_started_with_holdings():
    """보유 상태로 시작(initial_lots, 실전 '보유분 입력'과 같은 의미론)이면 부트스트랩 없음 — 콜드 스타트 전용."""
    from app.strategy.backtest import run_backtest
    from tests.test_strategy_backtest import make_bars

    b200, blev = make_bars(n=600), make_bars(n=600, ratio=0.3)
    r = run_backtest(b200, blev, 50_000_000, Params(), collect_plans=True,
                     initial_lots=[{"leg": "K200", "qty": 300, "price": int(b200[0]["close"])}])
    assert not any(o.kind == "boot" for p in r.plans for o in p.orders)
