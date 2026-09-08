"""무인 실행 2차 검증(2026-09-06)의 회귀 테스트 — ADR-009 단일 실행(2026-09-08)에 맞게 갱신: 승인·계획 재대조 테스트는 제거, 한도는 생략이 아니라 축소. DB 필요."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.broker import reconcile_plan
from tests.test_autoexec import DB_UP, KST, LINES, FakeKis, _client, _orders, _run, _setup_portfolio

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


def test_reconcile_kind_and_pause_only_on_dangerous_items():
    """Fix C: 대조 항목에 kind 가 붙고(level·text 불변), 무인 정지는 unplanned·excess 만 — 부분체결(short)은 정지 사유가 아니다."""
    import app.autoexec as ae

    plan = [{"kind": "grid1", "instrument": "K200", "side": "buy", "qty": 8, "price": 100000}]
    short = reconcile_plan(plan, [{"leg": "K200", "side": "buy", "qty": 5, "price": 100000}])
    assert short[0]["level"] == "warn" and short[0]["kind"] == "short" and "-3주" in short[0]["text"]
    excess = reconcile_plan(plan, [{"leg": "K200", "side": "buy", "qty": 11, "price": 100000}])
    assert excess[0]["kind"] == "excess"
    unplanned = reconcile_plan([], [{"leg": "LEV", "side": "buy", "qty": 1, "price": 9000}])
    assert unplanned[0]["kind"] == "unplanned"
    missing = reconcile_plan(plan, [])
    assert missing[0]["kind"] == "missing" and missing[0]["level"] == "info"

    c, h = _client()
    pid, aid = _setup_portfolio(c, h, datetime.now(KST).date() + timedelta(days=1))
    from app.db import SessionLocal
    from app.models import TradePortfolio
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid)
        assert ae.pause_if_reconcile_warns(s, pf, {"items": short}) is False        # 부분체결 → 정지 안 함
        assert ae.pause_if_reconcile_warns(s, pf, {"items": missing}) is False      # 미이행 → 정지 안 함
        assert ae.pause_if_reconcile_warns(s, pf, {"items": excess}) is True        # 초과 체결 → 정지
        assert "계획 8주 ≠ 등록 11주" in ae.pf_auto_state(pf)["paused_reason"]


def test_deposit_fallback_fills_shallow_grid_first_and_clips_deeper(monkeypatch):
    """Fix B → ADR-009: 매수가능조회가 실패하면 예수금 누적 규칙으로 — 얕은 그리드(높은 가격)부터 발주하고 넘치는 줄은 남은 예수금에 맞춰 축소(0 이면 생략)."""
    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False, "daily_buy_cap_pct": 0}, headers=h)
    # grid1 5×99,000=495,000 · grid2 3×98,000=294,000 · 예수금 600,000 → grid1 5주, grid2 는 잔여 105,000 → 1주로 축소
    fake = FakeKis(open_px=100000, deposit=600_000, holdings={})
    rec, _ = _run(fake, aid, today, LINES[:2])
    assert rec["submitted"] == 2 and rec["clipped"] == 1 and fake.placed == [("069500", "buy", 5, 99000), ("069500", "buy", 1, 98000)]
    st, _ = _orders(c, h, pid, today)
    assert st["grid2"]["status"] == "submitted" and "예수금(폴백)에 맞춰 3→1주" in st["grid2"]["message"]
    # 예수금이 첫 줄에도 못 미치면 첫 줄 축소, 나머지 생략
    c2, h2 = _client()
    pid2, aid2 = _setup_portfolio(c2, h2, today)
    c2.put("/settings/auto-exec", json={"buy": True, "sell": False, "daily_buy_cap_pct": 0}, headers=h2)
    fake2 = FakeKis(open_px=100000, deposit=200_000, holdings={})
    rec2, _ = _run(fake2, aid2, today, LINES[:2])
    assert rec2["submitted"] == 1 and rec2["skipped"] == 1 and fake2.placed == [("069500", "buy", 2, 99000)]
    st2, _ = _orders(c2, h2, pid2, today)
    assert "예수금 한도(폴백)" in st2["grid2"]["message"]


def test_precheck_ledger_vs_account_mismatch_skips_and_pauses():
    """후속 1: 09:01 에 앱 원장의 전략 종목 보유가 계좌 잔고와 다르면 그날 발주를 전부 생략하고 정지한다."""
    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 100000,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={"069500": 7})   # 계좌 7주 ≠ 원장 10주
    rec, _ = _run(fake, aid, today, LINES[:3])
    assert rec["submitted"] == 0 and rec["skipped"] == 3 and fake.placed == []
    view = c.get(f"/portfolio/{pid}/auto-exec?date={today.isoformat()}", headers=h).json()
    assert view["paused"] is True and "원장 10주 ≠ 계좌 7주" in view["paused_reason"] and view["state"]["code"] == "paused"
    st, _ = _orders(c, h, pid, today)
    assert all("사전 대조 불일치" in i["message"] for i in st.values())


def test_buyable_check_before_each_buy_clips_to_available():
    """매수가능조회(2026-09-06 지시 → ADR-009 축소): 매수 줄마다 발주 직전 KIS 주문가능 수량으로 판정 — 앞 주문이 묶은 금액이 다음 판정에 반영되고,
    예수금 총액이 작아도 KIS 가 가능하다고 하면(매도대금 재사용 등) 발주된다. 가능 수량이 계획보다 적으면 그 수량으로 축소, 0 이면 생략."""
    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False, "daily_buy_cap_pct": 0}, headers=h)
    # 예수금 총액은 0 이지만 KIS 주문가능현금 500,000 → grid1(495,000) 5주 발주 후 잔여 5,000 → grid2 는 0주 → 생략
    fake = FakeKis(open_px=100000, deposit=0, holdings={}, psbl_cash=500_000)
    rec, _ = _run(fake, aid, today, LINES[:2])
    assert rec["submitted"] == 1 and rec["skipped"] == 1 and fake.placed == [("069500", "buy", 5, 99000)]
    assert fake.buyable_calls == [("069500", 99000), ("069500", 98000)]          # 얕은 단부터, 발주 직전마다 조회
    st, _ = _orders(c, h, pid, today)
    assert st["grid2"]["status"] == "skipped" and "주문가능 수량 부족" in st["grid2"]["message"] and "가능 0주" in st["grid2"]["message"]
