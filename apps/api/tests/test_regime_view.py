"""레짐 표시 보조 (2026-09-13, 검증서 제안 1·3 — 표시 전용) — 중립 세분화 라벨·레버리지 차단 사유.

회귀 조건: 이 모듈은 **판정에 손대지 않는다**. 같은 지표를 넣어도 next_regime 결과가 달라지지 않고,
라벨은 예측이 아니라 설명이다(검증서 §2: 조정→하락 3/상승 2, 회복→하락 6/상승 7 — 예측력 없음).
"""
from __future__ import annotations

from app.regime_view import detail, leverage_block, neutral_sub
from app.strategy.params import Params

P = Params()


def _ind(close=100.0, ma200=100.0, ma20=100.0, ma60=100.0, sigma20=0.2):
    return {"close": close, "ma200": ma200, "ma20": ma20, "ma60": ma60, "sigma20": sigma20}


def test_neutral_splits_into_correction_recovery_and_boundary():
    # 조정 — 추세는 MA200 위(L>0)인데 단기는 아래(S≤0): 상승장에서 내려온 직후
    sub = neutral_sub("NEUTRAL", _ind(close=120, ma200=100, ma20=95, ma60=100))
    assert sub["name"] == "조정" and sub["l"] == 0.2 and sub["s"] == -0.05 and "눌림" in sub["note"]
    # 회복 — L<0, S≥0: 하락장에서 올라온 직후
    assert neutral_sub("NEUTRAL", _ind(close=90, ma200=100, ma20=105, ma60=100))["name"] == "회복"
    # 경계 — 두 지표가 같은 방향
    assert neutral_sub("NEUTRAL", _ind(close=90, ma200=100, ma20=95, ma60=100))["name"] == "경계"
    assert neutral_sub("NEUTRAL", _ind(close=120, ma200=100, ma20=105, ma60=100))["name"] == "경계"


def test_sub_state_is_only_for_neutral_and_needs_indicators():
    assert neutral_sub("BULL", _ind(close=120, ma200=100)) is None
    assert neutral_sub("BEAR", _ind(close=80, ma200=100)) is None
    assert neutral_sub("NEUTRAL", None) is None
    assert neutral_sub("NEUTRAL", {"close": 100}) is None          # MA 없음
    assert neutral_sub("NEUTRAL", _ind(ma200=0)) is None           # 0 나눗셈 방지


def test_leverage_block_reports_the_control_that_is_already_binding():
    # 하락장·중립장 — 레버리지는 상승장에만 존재
    assert leverage_block("BEAR", _ind(), 0.2, P)["code"] == "regime"
    assert "하락장" in leverage_block("BEAR", _ind(), 0.2, P)["text"]
    assert "중립장" in leverage_block("NEUTRAL", _ind(), 0.65, P)["text"]
    # 상승장인데 σ20 이 청산선을 넘은 경우
    hi = leverage_block("BULL", _ind(sigma20=0.38), 1.3, P)
    assert hi["code"] == "sigma" and "38%" in hi["text"] and "35%" in hi["text"]
    # 상승장·σ 정상인데 E ≤ 1 — 레버리지 목표 0
    assert leverage_block("BULL", _ind(sigma20=0.2), 0.95, P)["code"] == "e"
    # 막혀 있지 않으면 표시할 것이 없다
    assert leverage_block("BULL", _ind(sigma20=0.2), 1.25, P) is None


def test_detail_bundles_both_and_stays_display_only():
    d = detail("NEUTRAL", _ind(close=120, ma200=100, ma20=95, ma60=100), 0.65, P)
    assert d["sub"]["name"] == "조정" and d["risk"]["code"] == "regime"
    assert set(d) == {"sub", "risk"}                                # 응답에 다른 값을 끼워 넣지 않는다
    # 판정 함수는 이 모듈을 쓰지 않는다 — 같은 입력에서 레짐은 그대로
    from app.strategy.regime import Regime, next_regime

    assert next_regime(Regime.NEUTRAL, 120, 95, 100, 100, P) is Regime.NEUTRAL
