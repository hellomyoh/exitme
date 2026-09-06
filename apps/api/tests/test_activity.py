"""활동 로그 / 로그 페이지 (2026-09-06 지시 "로깅 기능") — 거래·주문·이벤트 병합, 필터, 격리, 기록 지점(무인 실행·승인·예약주문·거래 삭제)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]
KST = timezone(timedelta(hours=9))


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"lg{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_logs_merge_filters_and_isolation():
    """거래(원장)·주문(BrokerOrder)·이벤트(ActivityLog)를 합쳐 최신순, type/level/q/portfolio 필터, 다른 사용자에게 보이지 않음, 거래 삭제 이벤트."""
    from app.activity import log_event
    from app.models import BrokerOrder, TradePortfolio

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "로그포트", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    d0 = (datetime.now(KST).date() - timedelta(days=3)).isoformat()
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000, "executed_at": d0 + "T15:30:00+09:00"}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 5, "price": 100_000,
                               "executed_at": d0 + "T15:31:00+09:00", "memo": "테스트 매수"}, headers=h)
    sell_id = c.post("/positions", json={"portfolio_id": pid, "kind": "sell", "code": "069500", "qty": 2, "price": 110_000,
                                         "executed_at": (datetime.now(KST).date() - timedelta(days=1)).isoformat() + "T15:30:00+09:00"}, headers=h).json()["id"]
    with SessionLocal() as s:
        uid = s.get(TradePortfolio, pid).user_id
        s.add(BrokerOrder(portfolio_id=pid, broker_credential_id=None, plan_date=datetime.now(KST).date(), line_key="grid1:K200:buy:limit:99000",
                          code="069500", instrument="K200", kind="grid1", side="buy", otype="limit", qty=5, price=99_000,
                          status="failed", mode="auto", message="KIS error 40310000 주문가능금액을 초과하였습니다"))
        s.add(BrokerOrder(portfolio_id=pid, broker_credential_id=None, plan_date=datetime.now(KST).date(), line_key="tp:K200:sell:limit:103000",
                          code="069500", instrument="K200", kind="tp", side="sell", otype="limit", qty=2, price=103_000,
                          status="reserved", mode="reserve", rsvn_ord_seq="7"))
        log_event(s, uid, "sync.post_close", "장 마감 동기화 15:45 — 체결 1건 조회 · 신규 1건 등록", portfolio_id=pid)
        log_event(s, uid, "autoexec.paused", "무인 실행 정지 — 발주 연속 실패 2회", level="error", portfolio_id=pid)
        s.commit()

    j = c.get("/logs", headers=h).json()
    assert j["total"] == 7 and j["counts"] == {"info": 5, "warn": 0, "error": 2}
    ats = [i["at"] for i in j["items"]]
    assert ats == sorted(ats, reverse=True) and all(a.endswith("+09:00") for a in ats)   # 최신순 · KST 표기
    types = {i["type"] for i in j["items"]}
    assert types == {"trade", "order", "event"}
    trade = next(i for i in j["items"] if i["type"] == "trade" and i["kind"] == "buy")
    assert trade["text"] == "매수 KODEX 200 5주 @100,000원" and trade["detail"] == "테스트 매수" and trade["portfolio"] == "로그포트"
    sell = next(i for i in j["items"] if i["type"] == "trade" and i["kind"] == "sell")
    assert "실현 +20,000원" in sell["text"]
    order = next(i for i in j["items"] if i["type"] == "order" and i["kind"] == "failed")
    assert order["level"] == "error" and "무인 · 그리드 1차 매수 069500 5주 @99,000원 → 접수 실패" == order["text"] and "40310000" in order["detail"]
    # 필터
    assert c.get("/logs?type=trade", headers=h).json()["total"] == 3
    assert c.get("/logs?type=order", headers=h).json()["total"] == 2
    assert {i["kind"] for i in c.get("/logs?level=warn", headers=h).json()["items"]} == {"failed", "autoexec.paused"}
    assert c.get("/logs?level=error", headers=h).json()["total"] == 2
    assert c.get("/logs?q=테스트", headers=h).json()["total"] == 1
    assert c.get(f"/logs?portfolio_id={pid}", headers=h).json()["total"] == 7
    assert c.get("/logs?days=2&type=trade", headers=h).json()["total"] == 1   # 어제 매도만 (3일 전 입금·매수는 범위 밖)
    # 격리
    c2, h2 = _client()
    assert c2.get("/logs", headers=h2).json()["total"] == 0
    assert c2.get(f"/logs?portfolio_id={pid}", headers=h2).status_code == 404
    # 거래 삭제 → 원장에서 사라지고 '거래 삭제' 이벤트가 남는다
    assert c.delete(f"/positions/{sell_id}", headers=h).status_code == 200
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "tx.delete"]
    assert len(ev) == 1 and ev[0]["level"] == "warn" and "거래 삭제 — 매도 KODEX 200 2주 @110,000" in ev[0]["text"]
    assert c.get("/logs?type=trade", headers=h).json()["total"] == 2


def test_autoexec_approve_and_run_write_events(monkeypatch):
    """무인 실행 승인·실행 요약이 이벤트로 남는다 (줄별 결과는 주문 원천에서)."""
    import app.autoexec as ae
    from tests.test_autoexec import LINES, FakeKis, _setup_portfolio

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, LINES, gap_exact=None)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    r = c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h)
    assert r.status_code == 200 and r.json()["approved"] == 2
    ev = c.get("/logs?type=event", headers=h).json()["items"]
    assert any(i["kind"] == "autoexec.approve" and "무인 실행 승인 2건" in i["text"] for i in ev)
    # 실행은 DB 에 남은 다른 테스트의 오늘 승인 줄도 함께 처리한다 — 이 계좌만 넉넉한 가짜 클라이언트를 받게 해 결과를 고정한다
    fake = FakeKis(open_px=100_000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    idle = FakeKis(open_px=100_000, deposit=0, holdings={})
    with SessionLocal() as s:
        ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST),
                              client_factory=lambda cred: fake if cred.id == aid else idle, sleep_fn=lambda _s: None)
    ev = c.get("/logs?type=event", headers=h).json()["items"]
    run = next(i for i in ev if i["kind"] == "autoexec.run")
    assert run["level"] == "info" and "발주 2건" in run["text"] and run["portfolio"] == "무인"
    # 주문 원천에서 발주 줄이 보인다
    orders = c.get("/logs?type=order", headers=h).json()["items"]
    assert sum(1 for i in orders if i["kind"] == "submitted") == 2


def test_reserve_and_cancel_write_events(monkeypatch):
    """예약주문 접수·취소 이벤트."""
    import app.broker as broker
    from tests.test_broker import _fake_kis, _plan_setup

    c, h = _client()
    _fake_kis(monkeypatch)
    exec_day = (datetime.now(KST).date() + timedelta(days=1)).isoformat()
    pid, _acct, orders = _plan_setup(c, h, exec_day)
    monkeypatch.setattr(broker, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "ok"})
    r = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": orders[:2]}, headers=h).json()
    assert r["reserved"] == 2
    ev = c.get("/logs?type=event", headers=h).json()["items"]
    assert any(i["kind"] == "order.reserve" and "예약주문 접수 2건" in i["text"] and i["level"] == "info" for i in ev)
    oid = r["items"][0]["id"]
    assert c.post(f"/portfolio/{pid}/orders/{oid}/cancel", headers=h).status_code == 200
    ev = c.get("/logs?type=event&level=warn", headers=h).json()["items"]
    assert any(i["kind"] == "order.cancel" and "예약주문 취소" in i["text"] for i in ev)
