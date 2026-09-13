"""레짐 표시 보조 — **표시 전용**. 판정 공식·주문 규칙을 바꾸지 않는다.

근거: `docs/regime-review-verification-20260913.md` 의 제안 1·3 (표시로 채택, 배분 차등 없음).
  · 제안 1 — 중립을 조정·회복·경계로 갈라 보여 준다. 두 중립은 전략의 실제 상태가 다르다
    (전략 일변동 1.20% vs 0.53%, 20일 적중 42% vs 62%). 다만 **다음 레짐을 예측하지 못하므로**
    라벨은 "지금 어떤 상태인가"의 설명이지 신호가 아니다 — 문구도 그렇게 쓴다.
  · 제안 3 — 레버리지가 막힌 이유(하락장 · σ20 초과 · E ≤ 1)를 한 줄로 보여 준다. 이미 있는 통제를
    드러낼 뿐이라 서버 계산이 늘지 않는다(응답의 indicators·e_target·regime 만 쓴다).

이 모듈은 순수 함수다. 값이 없으면 None 을 돌려주고, 화면은 그 줄을 그리지 않는다.
"""
from __future__ import annotations

from app.strategy.params import Params

SUB_NOTE = {
    "조정": "상승 추세 속 눌림 — 상승장에서 내려온 직후라 보유가 많다",
    "회복": "하락 추세 속 반등 — 하락장에서 올라온 직후라 보유가 적다",
    "경계": "두 지표가 같은 방향 — 표본이 적은 드문 구간",
}


def neutral_sub(regime: str | None, ind: dict | None) -> dict | None:
    """중립의 세부 상태 — L = 종가/MA200 − 1, S = MA20/MA60 − 1.

    조정 = L>0·S≤0 (추세는 위, 단기는 아래) / 회복 = L<0·S≥0 / 그 밖 = 경계.
    중립이 아니거나 지표가 없으면 None.
    """
    if regime != "NEUTRAL" or not ind:
        return None
    close, ma200, ma20, ma60 = ind.get("close"), ind.get("ma200"), ind.get("ma20"), ind.get("ma60")
    if not all(isinstance(v, (int, float)) and v for v in (close, ma200, ma20, ma60)):
        return None
    l, s = close / ma200 - 1, ma20 / ma60 - 1
    name = "조정" if (l > 0 and s <= 0) else ("회복" if (l < 0 and s >= 0) else "경계")
    return {"name": name, "note": SUB_NOTE[name], "l": round(l, 4), "s": round(s, 4)}


def leverage_block(regime: str | None, ind: dict | None, e_target: float | None,
                   params: Params | None = None) -> dict | None:
    """레버리지가 막혀 있으면 그 사유 — 이미 작동 중인 통제의 설명 (새 규칙 아님).

    우선순위는 플래너의 판정 순서와 같다: 하락장/중립장(상승장이 아님) → σ20 초과 → E ≤ 1.
    막혀 있지 않으면 None.
    """
    p = params or Params()
    sigma = (ind or {}).get("sigma20")
    if regime and regime != "BULL":
        return {"code": "regime", "text": f"{'하락장' if regime == 'BEAR' else '중립장'} — 레버리지 보유 없음"}
    if isinstance(sigma, (int, float)) and sigma > p.sigma20_liquidate:
        return {"code": "sigma", "text": f"σ20 {sigma:.0%} > {p.sigma20_liquidate:.0%} — 레버리지 청산"}
    if e_target is not None and e_target <= 1.0:
        return {"code": "e", "text": f"E {e_target:.2f} ≤ 1.00 — 레버리지 목표 0"}
    return None


def detail(regime: str | None, ind: dict | None, e_target: float | None,
           params: Params | None = None) -> dict:
    """응답에 실을 표시용 묶음 — {"sub": …|None, "risk": …|None}."""
    return {"sub": neutral_sub(regime, ind),
            "risk": leverage_block(regime, ind, e_target, params)}
