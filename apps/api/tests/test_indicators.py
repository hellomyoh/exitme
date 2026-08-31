"""지표 단위 테스트 — 수기 계산 대조 (feature-chart §12, 전략 정본 §3)."""
import math

from app.strategy import indicators as ind


def test_sma_hand_calc():
    out = ind.sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2:] == [2.0, 3.0, 4.0]


def test_ema_matches_recurrence():
    xs = [10.0, 11.0, 12.0, 11.5]
    out = ind.ema(xs, 3)  # alpha = 0.5
    assert out[0] == 10.0
    assert out[1] == 10.5
    assert out[2] == 11.25
    assert out[3] == 11.375


def test_atr_wilder_hand_calc():
    high = [12.0, 13.0, 14.0, 13.5]
    low = [10.0, 11.0, 12.0, 12.5]
    close = [11.0, 12.0, 13.0, 13.0]
    # TR = [2, 2, 2, 1] / n=2: atr[1] = 2, atr[2] = (2*1+2)/2 = 2, atr[3] = (2*1+1)/2 = 1.5
    out = ind.atr(high, low, close, 2)
    assert out[0] is None
    assert out[1] == 2.0 and out[2] == 2.0 and out[3] == 1.5


def test_rsi_all_gains_is_100():
    close = [float(i) for i in range(1, 20)]
    out = ind.rsi(close, 14)
    assert out[14] == 100.0


def test_downside_vol_zero_when_no_down_days():
    close = [100.0 * (1.01 ** i) for i in range(30)]
    out = ind.rolling_vol_annualized(close, 20, downside_only=True)
    assert out[-1] == 0.0  # σ_down=0 케이스 — 전략 계층에서 floor 적용 대상


def test_total_vol_positive_and_annualized():
    close = [100, 102, 99, 103, 101, 104, 100, 105, 102, 106,
             103, 107, 104, 108, 105, 109, 106, 110, 107, 111, 108]
    out = ind.rolling_vol_annualized([float(c) for c in close], 20)
    assert out[20] is not None and out[20] > 0
    # 일간 σ ≈ 0.021 수준 → 연율화 √252 배
    daily = out[20] / math.sqrt(252)
    assert 0.005 < daily < 0.05
