"""TF(추세 필터 보유) 전략 테스트 — 2026-08-31 시장별 분리."""
import pytest

from app.strategy.trendfilter import TF_EXIT_BUFFER, TF_MA, run_tf_backtest
from tests.test_strategy_backtest import make_bars


def test_tf_holds_in_uptrend_and_exits_on_break():
    bars = make_bars(n=600, drift=0.002)  # 강한 상승 합성
    r = run_tf_backtest(bars, 100_000_000)
    assert r.kpi["total_return"] > 0
    assert "BULL" in r.regimes
    # 상승 추세에서는 대부분 보유
    assert sum(1 for x in r.regimes if x == "BULL") > len(r.regimes) * 0.5
    # 체결은 시장가 전환 쌍 — 매수·매도 수량 짝 검증
    buys = [f for f in r.fills if f.side == "buy"]
    sells = [f for f in r.fills if f.side == "sell"]
    assert len(buys) >= 1 and len(buys) - len(sells) in (0, 1)


def test_tf_hysteresis_no_flapping():
    """MA200 바로 아래 2% 이내 하락으로는 청산하지 않는다."""
    bars = make_bars(n=600, drift=0.002)
    r = run_tf_backtest(bars, 100_000_000)
    for p in r.plans:
        if p.status != "OK" or not p.orders:
            continue
        o = p.orders[0]
        ind = p.indicators
        if o.kind == "tf_exit":
            assert ind["close"] < ind["ma200"] * (1 - TF_EXIT_BUFFER) + 1e-9
        if o.kind == "tf_entry":
            assert ind["close"] > ind["ma200"] - 1e-9


def test_tf_downtrend_stays_cash():
    bars = make_bars(n=700, seed=11, drift=-0.002)
    r = run_tf_backtest(bars, 100_000_000)
    # 하락장 막바지엔 현금 대기
    assert r.regimes[-1] == "NEUTRAL" and r.qty_200[-1] == 0
    assert r.kpi["mdd"] > -0.35  # 매수보유식 붕괴 없음


def test_tf_reproducible():
    bars = make_bars(n=600)
    r1 = run_tf_backtest(bars, 100_000_000)
    r2 = run_tf_backtest(bars, 100_000_000)
    assert r1.equity == r2.equity
