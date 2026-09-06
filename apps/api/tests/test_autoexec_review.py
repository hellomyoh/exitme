"""무인 실행 2차 검증 (2026-09-06 지시 "논리·절차 오류 검토") 에서 고친 3건의 회귀 테스트. DB 필요."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.broker import reconcile_plan
from tests.test_autoexec import DB_UP, KST, LINES, FakeKis, _client, _setup_portfolio

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
    pid, _ = _setup_portfolio(c, h, datetime.now(KST).date() + timedelta(days=1), LINES, gap_exact=None)
    from app.db import SessionLocal
    from app.models import TradePortfolio
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid)
        assert ae.pause_if_reconcile_warns(s, pf, {"items": short}) is False        # 부분체결 → 정지 안 함
        assert ae.pause_if_reconcile_warns(s, pf, {"items": missing}) is False      # 미이행 → 정지 안 함
        assert ae.pause_if_reconcile_warns(s, pf, {"items": excess}) is True        # 초과 체결 → 정지
        assert "계획 8주 ≠ 등록 11주" in ae.pf_auto_state(pf)["paused_reason"]


def test_reserve_refuses_line_already_approved_for_auto(monkeypatch):
    """Fix A: 무인 승인된 줄은 예약주문으로 다시 접수되지 않는다(이중 발주 방지) — 반대 방향은 승인 쪽에서 이미 막힌다."""
    import app.autoexec as ae
    import app.broker as br

    c, h = _client()
    tomorrow = datetime.now(KST).date() + timedelta(days=1)
    pid, _ = _setup_portfolio(c, h, tomorrow, LINES, gap_exact=97500.0)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    assert c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[0]]}, headers=h).json()["approved"] == 1
    # 예약 접수 창을 열어 두고(시간 무관) 같은 줄을 예약 → duplicate, 다른 줄(grid2)은 접수 시도
    monkeypatch.setattr(br, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "test"})
    monkeypatch.setattr(br, "_client", lambda cred: FakeKis(open_px=0, deposit=0, holdings={}))

    class _Resv(FakeKis):
        def reserve_order(self, code, side, qty, price, end_date=None):
            return {"rsvn_ord_seq": "84617", "msg": "ok", "raw": {}}
    monkeypatch.setattr(br, "_client", lambda cred: _Resv(open_px=0, deposit=0, holdings={}))
    r = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": tomorrow.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h).json()
    st = {i["kind"]: i["status"] for i in r["items"]}
    assert st["grid1"] == "duplicate" and st["grid2"] == "reserved"
    # 반대: 예약된 grid2 를 무인 승인하려 하면 duplicate
    r2 = c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[1]]}, headers=h).json()
    assert r2["items"][0]["status"] == "duplicate" and r2["approved"] == 0
    del ae  # noqa: F821 — 참조 유지용


def test_deposit_limit_fills_shallow_grid_first(monkeypatch):
    """Fix B: 예수금이 일부만 되면 얕은 그리드(높은 가격)부터 발주하고 넘치는 줄만 생략 — 전부 생략하지 않는다."""
    import app.autoexec as ae

    c, h = _client()
    today = datetime.now(KST).date()
    pid, _ = _setup_portfolio(c, h, today, LINES, gap_exact=None)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h)
    # grid1 5×99,000=495,000 · grid2 3×98,000=294,000 · 예수금 600,000 → grid1 만 발주
    fake = FakeKis(open_px=100000, deposit=600_000, holdings={})
    from app.db import SessionLocal
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    rec = out["portfolios"][0]
    assert rec["submitted"] == 1 and rec["skipped"] == 1 and fake.placed == [("069500", "buy", 5, 99000)]
    st = {i["kind"]: i for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]}
    assert st["grid1"]["status"] == "submitted" and st["grid2"]["status"] == "skipped" and "예수금 한도(폴백)" in st["grid2"]["message"]


def test_precheck_ledger_vs_account_mismatch_skips_and_pauses(monkeypatch):
    """후속 1: 09:01 에 앱 원장의 전략 종목 보유가 계좌 잔고와 다르면 그날 발주를 전부 생략하고 정지한다."""
    import app.autoexec as ae
    from app.db import SessionLocal

    c, h = _client()
    today = datetime.now(KST).date()
    pid, _ = _setup_portfolio(c, h, today, LINES, gap_exact=None)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 100000,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": LINES[:3]}, headers=h)
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={"069500": 7})   # 계좌 7주 ≠ 원장 10주
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    rec = out["portfolios"][0]
    assert rec["submitted"] == 0 and rec["skipped"] == 3 and fake.placed == []
    view = c.get(f"/portfolio/{pid}/auto-exec", headers=h).json()
    assert view["paused"] is True and "원장 10주 ≠ 계좌 7주" in view["paused_reason"]
    assert all("사전 대조 불일치" in i["message"] for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"])


def test_precheck_plan_revalidation_at_execution(monkeypatch):
    """후속 2: 승인 뒤 계획 스냅샷이 바뀌면(수량 변경) 그 줄은 실행 시점 재대조에서 생략되고 나머지는 발주된다."""
    import app.autoexec as ae
    from app.db import SessionLocal
    from app.models import PortfolioPlan

    c, h = _client()
    today = datetime.now(KST).date()
    pid, _ = _setup_portfolio(c, h, today, LINES, gap_exact=None)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h)
    with SessionLocal() as s:   # 계획의 grid1 수량을 5 → 6 으로 바꿔 승인 행과 어긋나게
        plan = s.scalar(ae.select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pid, PortfolioPlan.trade_date == today))
        orders = [dict(o, qty=6) if o["kind"] == "grid1" else o for o in plan.payload["orders"]]
        plan.payload = {**plan.payload, "orders": orders}
        s.commit()
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={})
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    rec = out["portfolios"][0]
    assert rec["submitted"] == 1 and rec["skipped"] == 1 and fake.placed == [("069500", "buy", 3, 98000)]
    st = {i["kind"]: i for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]}
    assert st["grid1"]["status"] == "skipped" and "재대조 실패" in st["grid1"]["message"] and st["grid2"]["status"] == "submitted"


def test_buyable_check_before_each_buy(monkeypatch):
    """매수가능조회(2026-09-06 지시): 매수 줄마다 발주 직전 KIS 주문가능 수량으로 판정 — 앞 주문이 묶은 금액이 다음 판정에 반영되고,
    예수금 총액이 작아도 KIS 가 가능하다고 하면(매도대금 재사용 등) 발주된다. 수량을 줄여 내지 않는다."""
    import app.autoexec as ae
    from app.db import SessionLocal

    c, h = _client()
    today = datetime.now(KST).date()
    pid, _ = _setup_portfolio(c, h, today, LINES, gap_exact=None)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h)
    # 예수금 총액은 0 이지만 KIS 주문가능현금 600,000 → grid1(495,000) 발주 후 잔여 105,000 → grid2(294,000) 는 수량 부족으로 생략
    fake = FakeKis(open_px=100000, deposit=0, holdings={}, psbl_cash=600_000)
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    rec = out["portfolios"][0]
    assert rec["submitted"] == 1 and rec["skipped"] == 1 and fake.placed == [("069500", "buy", 5, 99000)]
    assert fake.buyable_calls == [("069500", 99000), ("069500", 98000)]          # 얕은 단부터, 발주 직전마다 조회
    st = {i["kind"]: i for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]}
    assert st["grid2"]["status"] == "skipped" and "주문가능 수량 부족" in st["grid2"]["message"] and "가능 1주 < 계획 3주" in st["grid2"]["message"]
