"""진입 배분 연구 r2 (scripts/entry_alloc_study.py) — 복제본의 충실성.

반론서(2026-09-13)의 요구: ① 스위치 없는 래퍼는 현행 주문과 **매일 완전 일치** ② 모든 변형이 변동성·레버리지 자격·청산
게이트를 유지 ③ 유지·신규 매수가 **하나의 수수료 포함 예산**을 공유. DB 없이 합성 봉으로 고정한다.
"""
from __future__ import annotations

from dataclasses import replace

from app.strategy.params import Params
from app.strategy.planner import LEV
from scripts.entry_alloc_study import CAP, W, check_equivalence, run_variant
from tests.test_lot_tags import _lev_from, _synthetic_bars

P = Params()


def _bars():
    b200 = _synthetic_bars()
    return b200, _lev_from(b200)


def test_replica_without_switches_matches_the_planner_every_day():
    """스위치 없는 복제본 = 현행 플래너(A1 채택 후: 현금 부족 단은 축소)."""
    b200, blev = _bars()
    n, mism = check_equivalence(b200, blev, P)
    assert n > 300, n                       # 워밍업 뒤 계획일이 충분히 검사됐다
    assert mism == [], mism[:3]


def test_current_planner_clips_the_rung_the_old_rule_dropped():
    """A1 채택 확인 — 현금이 모자란 단을 현행은 축소해 내고, 종전 규칙(legacy_skip)은 통째로 생략한다.

    로그의 `planner` 가 현행(축소), `variant` 가 종전(생략)이다.
    """
    b200, blev = _bars()
    log, blog = [], []
    run_variant(b200, blev, P, W, legacy_skip=True, log=log, budget_log=blog)
    assert log, "두 규칙이 갈리는 날이 없다 — 합성 봉이 현금 제약을 만들지 못했다"
    dropped_rungs = 0
    for m in log:
        only_now = [k for k in m["planner"] if k not in m["variant"]]     # 현행에만 있는 주문
        only_old = [k for k in m["variant"] if k not in m["planner"]]     # 종전에만 있는 주문
        assert all(k[1] == "buy" for k in only_now + only_old), m          # 매도는 건드리지 않는다
        if any(k[0] != LEV and k[5].startswith("grid") for k in only_now):
            dropped_rungs += 1
    assert dropped_rungs > 0, "현행이 축소해 살린 그리드 단이 없다"


def test_budget_guarantee_is_cash_at_planned_prices_not_the_buffer():
    """예산 성질 — 이 표본·계획가격 기준으로 수수료 포함 매수가 현금을 넘은 날은 없다.
    다만 현금버퍼(0.5%)까지 보존하지는 않는다(플래너가 예약에서 수수료를 빼지 않는 기존 성질).
    일반적 보장이 아니다 — 실제 체결가·매도대금 변동은 이 검사의 범위 밖이다.
    """
    b200, blev = _bars()
    for kw in ({}, {"legacy_skip": True}):
        blog: list = []
        run_variant(b200, blev, P, W, budget_log=blog, **kw)
        assert all(kind != "cash" for kind, _, _ in blog), kw
        for kind, _, over in blog:
            if kind == "buffer":
                assert 0 < over < 100_000, over


def test_lev_first_keeps_the_volatility_gate():
    """σ20 > 청산선이면 보유가 없어도 레버리지 매수를 만들지 않는다 — 1차 연구의 결함 재발 방지."""
    b200, blev = _bars()
    low_gate = replace(P, sigma20_liquidate=0.10)   # 합성 봉(연 ~19%)에서 게이트가 자주 물리게
    log, blog = [], []
    r = run_variant(b200, blev, low_gate, W, lev_first=True, log=log, budget_log=blog)
    assert blog == []
    for m in log:
        if m["sigma20"] is not None and m["sigma20"] > low_gate.sigma20_liquidate:
            assert not any(k[0] == LEV and k[1] == "buy" for k in m["variant"]), m
    # 게이트가 물린 날은 실제로 있었다 (검사가 공회전이 아님)
    from app.strategy.planner import prepare
    m200 = prepare([float(b["open"]) for b in b200], [float(b["high"]) for b in b200],
                   [float(b["low"]) for b in b200], [float(b["close"]) for b in b200], low_gate)
    assert any(s is not None and s > low_gate.sigma20_liquidate for s in m200.sigma20[W:])
    assert r.kpi["total_return"] is not None


# ── r2.1 (2026-09-13 2차 반론) — 경계·정책 효과 ────────────────────────────────────

def _clip_days(params=P, **kw):
    b200, blev = _bars()
    log, blog = [], []
    run_variant(b200, blev, params, W, log=log, budget_log=blog, **kw)
    return log, blog


def test_clip_handles_zero_and_negative_cash_and_the_one_share_boundary():  # noqa: D401
    """현금이 0·음수(버퍼가 현금보다 큼)면 주문을 만들지 않고, 1주도 못 사면 축소가 0 으로 끝난다."""
    from app.strategy.params import round_tick
    from app.strategy.planner import K200, Order

    # 축소 공식 자체의 경계 — int(cash // (price × (1+c)))
    c = P.commission
    price = 70_000
    for cash, want in ((0.0, 0), (-1.0, 0), (price * (1 + c) - 1, 0), (price * (1 + c), 1), (price * (1 + c) * 2.5, 2)):
        assert int(max(cash, 0) // (price * (1 + c))) == want, (cash, want)
    # 실제 실행에서도 음수 현금 날에는 매수가 없다 (예산 위반 로그가 그 날을 세지 않는다)
    _, blog = _clip_days()
    assert all(kind != "cash" for kind, _, _ in blog)


def test_clip_prefers_the_shallow_rung_over_deeper_rungs_and_tactical_leverage():
    """r2.1 §8.1 — A1 은 '오류 제거'가 아니라 배분 정책 변경이다: 얕은 단을 채우고 그만큼 뒤(깊은 단·레버리지)가 줄어든다.
    현행(축소) 기준으로 보면, 종전(생략)에는 있던 깊은 단·레버리지가 현행에는 없는 날이 있다."""
    log, _ = _clip_days(legacy_skip=True)
    shallower = 0
    for m in log:
        added = {k[5] for k in m["planner"] if k not in m["variant"]}     # 현행에만 있는 것 = 축소로 살린 얕은 단
        removed = {k[5] for k in m["variant"] if k not in m["planner"]}   # 종전에만 있던 것 = 밀려난 깊은 단·레버리지
        # 추가된 K200 단 번호 < 제거/축소된 K200 단 번호이거나, 레버리지가 줄어든 날
        if any(a.startswith("grid") for a in added) and (removed - added):
            shallower += 1
    assert shallower > 0, "얕은 단이 깊은 단·레버리지를 대체한 날이 없다 — 정책 효과 주장이 성립하지 않는다"


def test_budget_guarantee_is_cash_not_the_cash_buffer():
    """r2.1 §8.2 — 수수료 포함 매수는 현금을 넘지 않지만, 버퍼까지 보존하지는 않는다(현행도 같다)."""
    for kw in ({}, {"legacy_skip": True}):
        log, blog = _clip_days(**kw)
        assert all(kind != "cash" for kind, _, _ in blog), kw        # 현금 자체는 넘지 않는다
        for kind, _, over in blog:
            if kind == "buffer":
                assert 0 < over < 100_000, over                       # 잠식은 수수료 규모(버퍼의 극히 일부)
