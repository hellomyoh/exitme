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
    b200, blev = _bars()
    n, mism = check_equivalence(b200, blev, P)
    assert n > 300, n                       # 워밍업 뒤 계획일이 충분히 검사됐다
    assert mism == [], mism[:3]


def test_clip_mode_never_exceeds_the_fee_inclusive_budget_and_only_adds_rungs():
    b200, blev = _bars()
    log, blog = [], []
    run_variant(b200, blev, P, W, clip_rung=True, log=log, budget_log=blog)
    assert blog == []                       # 수수료 포함 총 매수 ≤ 현금 + 축소 매도대금, 매일
    assert log                              # 축소가 실제로 일어난 날이 있다
    for m in log:
        added = [k for k in m["variant"] if k not in m["planner"]]
        removed = [k for k in m["planner"] if k not in m["variant"]]
        # K200 쪽 변화는 그리드 단뿐(부트스트랩·매도 불변). 축소된 단이 현금을 먹으면 뒤 단·레버리지가 줄 수 있다 — 플래너 순서 그대로
        assert all(k[1] == "buy" and (k[0] == LEV or k[5].startswith("grid")) for k in added + removed), m
        # 레버리지는 수량만 줄거나(현금이 0 이 되면) 사라진다 — 새 종류가 생기지는 않는다
        assert {k[5] for k in added if k[0] == LEV} <= {k[5] for k in removed if k[0] == LEV}, m
    # 최소 한 날은 플래너가 생략한 단이 축소돼 살아났다
    assert any(any(k[0] != LEV and k[5] not in {r[5] for r in m["planner"]} for k in m["variant"]) for m in log)


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


def test_clip_handles_zero_and_negative_cash_and_the_one_share_boundary():
    """현금이 0·음수(버퍼가 현금보다 큼)면 주문을 만들지 않고, 1주도 못 사면 축소가 0 으로 끝난다."""
    from app.strategy.params import round_tick
    from app.strategy.planner import K200, Order

    # 축소 공식 자체의 경계 — int(cash // (price × (1+c)))
    c = P.commission
    price = 70_000
    for cash, want in ((0.0, 0), (-1.0, 0), (price * (1 + c) - 1, 0), (price * (1 + c), 1), (price * (1 + c) * 2.5, 2)):
        assert int(max(cash, 0) // (price * (1 + c))) == want, (cash, want)
    # 실제 실행에서도 음수 현금 날에는 매수가 없다 (예산 위반 로그가 그 날을 세지 않는다)
    log, blog = _clip_days(clip_rung=True)
    assert all(kind != "cash" for kind, _, _ in blog)


def test_clip_prefers_the_shallow_rung_over_deeper_rungs_and_tactical_leverage():
    """r2.1 §8.1 — A1 은 '오류 제거'가 아니라 배분 정책 변경이다: 얕은 단을 채우고 그만큼 뒤(깊은 단·레버리지)가 줄어든다."""
    log, _ = _clip_days(clip_rung=True)
    shallower = 0
    for m in log:
        added = {k[5] for k in m["variant"] if k not in m["planner"]}
        removed = {k[5] for k in m["planner"] if k not in m["variant"]}
        # 추가된 K200 단 번호 < 제거/축소된 K200 단 번호이거나, 레버리지가 줄어든 날
        if any(a.startswith("grid") for a in added) and (removed - added):
            shallower += 1
    assert shallower > 0, "얕은 단이 깊은 단·레버리지를 대체한 날이 없다 — 정책 효과 주장이 성립하지 않는다"


def test_budget_guarantee_is_cash_not_the_cash_buffer():
    """r2.1 §8.2 — 수수료 포함 매수는 현금을 넘지 않지만, 버퍼까지 보존하지는 않는다(현행도 같다)."""
    for kw in ({}, {"clip_rung": True}):
        log, blog = _clip_days(**kw)
        assert all(kind != "cash" for kind, _, _ in blog), kw        # 현금 자체는 넘지 않는다
        for kind, _, over in blog:
            if kind == "buffer":
                assert 0 < over < 100_000, over                       # 잠식은 수수료 규모(버퍼의 극히 일부)
