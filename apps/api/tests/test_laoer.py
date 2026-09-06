"""라오어 무한매수 v2.2/v3.0 · VR 재현 엔진 테스트 (2026-09-06) — 규칙 재현이 코드대로 동작하는지 합성 시계열로 확인."""
from __future__ import annotations

from app.strategy.laoer import V22, V30, VRParams, run_infinite, run_vr
from tests.test_ltv import _bars


def _ohlc(closes: list[float], wiggle: float = 0.01) -> list[dict]:
    out = []
    for b in _bars(closes):
        c = b["close"]
        out.append({**b, "high": c * (1 + wiggle), "low": c * (1 - wiggle)})
    return out


def test_infinite_v22_cycle_takes_profit_at_10pct_and_restarts():
    # 100 → 하락 → 반등 +12%: 평단 아래에서 LOC 매수가 쌓이고, 평단 +10% 지정가에 3/4 익절 → 사이클 종료 → 재시작
    closes = [100.0] + [100 - i for i in range(1, 15)] + [86 + 2 * i for i in range(1, 25)]
    r = run_infinite(_ohlc(closes), 100_000, V22)
    assert r.flips >= 2                                  # 사이클 2회 이상
    kinds = {t.kind for t in r.trades}
    assert "tp" in kinds                                 # +10% 지정가 익절 발생
    assert all(t.pnl > -1e-6 for t in r.trades if t.kind == "tp")
    assert min(r.equity) > 0 and r.avg_exposure < 0.6   # 40분할이라 노출은 낮다
    # 현금이 음수가 되는 매수는 없다
    assert all(e >= 0 for e in r.exposure)


def test_infinite_v22_quarter_stop_when_seed_exhausted():
    # 끝없는 하락: 40회차 소진 후 1/4 쿼터 매도로 시드를 재확보하고 계속 매수 (전량 손절 없음)
    closes = [100 * (0.99 ** i) for i in range(160)]
    r = run_infinite(_ohlc(closes), 100_000, V22)
    assert any(t.kind == "quarter" for t in r.trades)
    assert r.regime_on[-1] is True and r.equity[-1] < 100_000
    assert r.equity[-1] > 0


def test_infinite_v30_compounds_capital_after_profit():
    closes = [100.0] + [100 - i for i in range(1, 8)] + [93 + 3 * i for i in range(1, 20)]
    r22 = run_infinite(_ohlc(closes), 100_000, V22)
    r30 = run_infinite(_ohlc(closes), 100_000, V30)
    # v3.0 은 20분할(회당 2배 금액)·+15% 익절·복리 → 같은 경로에서 노출이 더 크다
    assert r30.avg_exposure > r22.avg_exposure


def test_vr_rebalances_only_outside_band_and_updates_v():
    # 10거래일마다 판정: 급등하면 상단(+15%) 초과분 매도, 급락하면 하단(−15%) 부족분을 풀 한도(50%) 안에서 매수
    flat = [100.0] * 30
    up = [100 * (1.02 ** i) for i in range(1, 30)]
    down = [up[-1] * (0.97 ** i) for i in range(1, 40)]
    r = run_vr(_ohlc(flat + up + down), 100_000, VRParams())
    kinds = [t.kind for t in r.trades]
    assert "vr_sell" in kinds                            # 급등 구간 매도
    assert r.rebalances >= 2 and r.rebalances < 15       # 밴드 안에서는 거래 없음
    assert r.exposure[0] < 0.55 and r.exposure[0] > 0.45  # 초기 50/50
    assert all(v > 0 for v in r.equity)
