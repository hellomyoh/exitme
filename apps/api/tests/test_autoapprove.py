"""완전 무인 운영 (2026-09-07 지시) — 자동 승인 설정·16:45 배치(지정가 승인·시장가 예약 접수·중복·정지·상한)·전량 취소(긴급 정지)."""
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

LINES = [
    {"instrument": "K200", "kind": "grid1", "side": "buy", "otype": "limit", "qty": 5, "price": 99000},
    {"instrument": "K200", "kind": "grid2", "side": "buy", "otype": "limit", "qty": 3, "price": 98000},
    {"instrument": "K200", "kind": "tp", "side": "sell", "otype": "limit", "qty": 2, "price": 103000},
    {"instrument": "LEV", "kind": "lev_strat", "side": "buy", "otype": "market", "qty": 4, "price": None},
]


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"aa{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _setup(c, h, exec_day: date, env="prod", lines=LINES):
    """실전 포트 + 계좌 연결 + 입금 + 실행일 계획 스냅샷."""
    from app.models import PortfolioPlan

    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01", "env": env}, headers=h).json()
    pid = c.post("/portfolios", json={"name": "완전무인", "market": "KR", "code_200": "069500", "credential_id": acct["id"]}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 5_000_000,
                               "executed_at": (datetime.now(KST).date() - timedelta(days=3)).isoformat() + "T15:30:00+09:00"}, headers=h)
    with SessionLocal() as s:
        s.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload={"regime": "NEUTRAL", "orders": lines, "gap_cancel_exact": 97000.0}))
        s.commit()
    return pid, acct["id"]


class FakeBroker:
    def __init__(self, fail_reserve=False):
        self.reserved: list[tuple] = []
        self.cancelled: list[tuple] = []
        self.rcancelled: list[tuple] = []
        self.fail_reserve = fail_reserve

    def reserve_order(self, code, side, qty, price, end_date=None):
        if self.fail_reserve:
            raise RuntimeError("KIS error APBK0919 주문가능금액을 초과하였습니다")
        self.reserved.append((code, side, qty, price))
        return {"rsvn_ord_seq": f"R{len(self.reserved)}", "msg": "예약주문이 접수되었습니다", "raw": {"RSVN_ORD_SEQ": f"R{len(self.reserved)}"}}

    def cancel_order(self, order_no, orgno=""):
        self.cancelled.append((order_no, orgno))
        return {"msg": "취소 완료", "raw": {}}

    def cancel_reserved_order(self, rsvn_ord_seq, ord_dt, orgno=""):
        self.rcancelled.append((rsvn_ord_seq, ord_dt))
        return {"msg": "예약주문이 취소되었습니다", "raw": {}}


def _run(exec_day: date, fake, acct_id: int, lines=LINES, now_hour=16):
    import app.autoapprove as aa

    today = datetime.now(KST).date()
    idle = FakeBroker()
    with SessionLocal() as s:
        return aa.run_auto_approve(s, now=datetime.combine(today, datetime.min.time(), tzinfo=KST).replace(hour=now_hour, minute=45),
                                   client_factory=lambda cred: fake if cred.id == acct_id else idle,
                                   plan_fn=lambda session, pf: {"exec_day": exec_day.isoformat(), "orders": lines, "status": "OK"})


def _rec(out, pid):
    return next((r for r in out["portfolios"] if r["portfolio_id"] == pid), None)


def test_auto_approve_setting_guards():
    """국내 포트·연결 계좌·설정 스위치가 있어야 켤 수 있고, 상태 응답에 설정이 실린다."""
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "미연결", "market": "KR"}, headers=h).json()["id"]
    r = c.put(f"/portfolio/{pid}/auto-exec/auto-approve", json={"enabled": True}, headers=h)
    assert r.status_code == 409 and "계좌" in r.json()["detail"]
    exec_day = _next_weekday(datetime.now(KST).date())
    pid2, _ = _setup(c, h, exec_day)
    r = c.put(f"/portfolio/{pid2}/auto-exec/auto-approve", json={"enabled": True}, headers=h)
    assert r.status_code == 409 and "무인 실행" in r.json()["detail"]
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    j = c.put(f"/portfolio/{pid2}/auto-exec/auto-approve", json={"enabled": True, "market_reserve": False, "daily_buy_cap": 20_000_000}, headers=h).json()
    assert j["auto_approve"]["enabled"] is True and j["auto_approve"]["market_reserve"] is False and j["auto_approve"]["daily_buy_cap"] == 20_000_000
    assert c.get(f"/portfolio/{pid2}/auto-exec", headers=h).json()["auto_approve"]["enabled"] is True
    us = c.post("/portfolios", json={"name": "미국", "market": "US"}, headers=h).json()["id"]
    assert c.put(f"/portfolio/{us}/auto-exec/auto-approve", json={"enabled": True}, headers=h).status_code == 409
    # 끄기는 조건 없이
    assert c.put(f"/portfolio/{pid2}/auto-exec/auto-approve", json={"enabled": False}, headers=h).json()["auto_approve"]["enabled"] is False
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.auto_approve_setting"]
    texts = [e["text"] for e in ev]
    assert len(ev) == 2 and any("완전 무인 운영 켬" in t and "하루 매수 상한 20,000,000원" in t for t in texts) and any("완전 무인 운영 끔" in t for t in texts)


def test_auto_approve_batch_approves_limits_reserves_market_and_is_idempotent(monkeypatch):
    """매수만 허용 + 시장가 예약 접수: 그리드 2줄 승인, 익절 매도는 '수동 필요', 레버리지 시장가는 예약주문 접수. 재실행은 중복 없음."""
    import app.autoapprove as aa

    c, h = _client()
    exec_day = _next_weekday(datetime.now(KST).date())
    pid, aid = _setup(c, h, exec_day)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    c.put(f"/portfolio/{pid}/auto-exec/auto-approve", json={"enabled": True, "market_reserve": True}, headers=h)
    monkeypatch.setattr(aa, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "ok"})
    fake = FakeBroker()
    rec = _rec(_run(exec_day, fake, aid), pid)
    assert rec["exec_day"] == exec_day.isoformat() and rec["approved"] == 2 and rec["reserved"] == 1 and rec["failed"] == 0
    assert rec["skipped"] == 1 and rec["manual"] == ["tp (매도 허용 꺼짐)"]
    assert fake.reserved == [("122630", "buy", 4, None)]
    bo = c.get(f"/portfolio/{pid}/orders?date={exec_day.isoformat()}", headers=h).json()
    st = {i["kind"]: i for i in bo["items"]}
    assert st["grid1"]["status"] == "approved" and st["grid1"]["mode"] == "auto" and "자동 승인" in st["grid1"]["message"]
    assert st["grid2"]["status"] == "approved" and st["lev_strat"]["status"] == "reserved" and st["lev_strat"]["mode"] == "reserve" and st["lev_strat"]["rsvn_ord_seq"] == "R1"
    assert "tp" not in st
    last = bo["auto_exec"]["auto_approve_last"]
    assert last["exec_day"] == exec_day.isoformat() and last["approved"] == 2 and last["reserved"] == 1 and last["manual"] == ["tp (매도 허용 꺼짐)"]
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.auto_approve"]
    assert ev and "지정가 2건 승인" in ev[0]["text"] and "시장가 1건 예약주문 접수" in ev[0]["text"] and "수동 필요 1건" in ev[0]["text"] and ev[0]["level"] == "warn"
    # 재실행 — 이미 살아 있는 줄은 건너뜀, 예약 재접수 없음
    rec2 = _rec(_run(exec_day, fake, aid), pid)
    assert rec2["approved"] == 0 and rec2["reserved"] == 0 and rec2["skipped"] == 4 and len(fake.reserved) == 1
    # 매도도 켜면 다음 실행에 익절 줄이 승인된다
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    rec3 = _rec(_run(exec_day, fake, aid), pid)
    assert rec3["approved"] == 1 and rec3["manual"] == []
    st = {i["kind"]: i for i in c.get(f"/portfolio/{pid}/orders?date={exec_day.isoformat()}", headers=h).json()["items"]}
    assert st["tp"]["status"] == "approved"


def test_auto_approve_skips_paused_stale_plan_market_off_vps_and_cap(monkeypatch):
    """정지 상태·실행일 지남·시장가 옵션 꺼짐·모의 계좌·하루 매수 상한 초과(정지)."""
    import app.autoapprove as aa

    monkeypatch.setattr(aa, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "ok"})
    exec_day = _next_weekday(datetime.now(KST).date())
    # 시장가 옵션 꺼짐 → 레버리지 줄은 수동 필요
    c, h = _client()
    pid, aid = _setup(c, h, exec_day)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    c.put(f"/portfolio/{pid}/auto-exec/auto-approve", json={"enabled": True, "market_reserve": False}, headers=h)
    fake = FakeBroker()
    rec = _rec(_run(exec_day, fake, aid), pid)
    assert rec["approved"] == 3 and rec["reserved"] == 0 and rec["manual"] == ["lev_strat (시장가 — 수동)"] and fake.reserved == []
    # 실행일이 오늘 이전 → 건너뜀 (오늘 일봉 미적재)
    c2, h2 = _client()
    pid2, aid2 = _setup(c2, h2, datetime.now(KST).date())
    c2.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h2)
    c2.put(f"/portfolio/{pid2}/auto-exec/auto-approve", json={"enabled": True}, headers=h2)
    rec2 = _rec(_run(datetime.now(KST).date(), FakeBroker(), aid2), pid2)
    assert rec2["approved"] == 0 and "실행일이 지났음" in rec2["note"]
    # 모의 계좌 → 지정가는 승인, 시장가 예약은 불가(수동 필요)
    c3, h3 = _client()
    pid3, aid3 = _setup(c3, h3, exec_day, env="vps")
    c3.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h3)
    c3.put(f"/portfolio/{pid3}/auto-exec/auto-approve", json={"enabled": True, "market_reserve": True}, headers=h3)
    fake3 = FakeBroker()
    rec3 = _rec(_run(exec_day, fake3, aid3), pid3)
    assert rec3["approved"] == 3 and rec3["reserved"] == 0 and "모의 계좌" in rec3["manual"][0] and fake3.reserved == []
    # 하루 매수 상한 초과 → 승인 0 + 정지 + 오류 로그
    c4, h4 = _client()
    pid4, aid4 = _setup(c4, h4, exec_day)
    c4.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h4)
    c4.put(f"/portfolio/{pid4}/auto-exec/auto-approve", json={"enabled": True, "market_reserve": False, "daily_buy_cap": 500_000}, headers=h4)
    rec4 = _rec(_run(exec_day, FakeBroker(), aid4), pid4)
    assert rec4["approved"] == 0 and "상한" in rec4["note"]
    view = c4.get(f"/portfolio/{pid4}/auto-exec", headers=h4).json()
    assert view["paused"] is True and "하루 상한" in view["paused_reason"]
    assert c4.get(f"/portfolio/{pid4}/orders?date={exec_day.isoformat()}", headers=h4).json()["items"] == []
    # 정지 상태에서는 건너뜀
    rec5 = _rec(_run(exec_day, FakeBroker(), aid4), pid4)
    assert rec5["note"] == "정지 상태"
    # 자동 승인이 꺼진 포트는 목록에 없다
    c4.put(f"/portfolio/{pid4}/auto-exec/auto-approve", json={"enabled": False}, headers=h4)
    assert _rec(_run(exec_day, FakeBroker(), aid4), pid4) is None


def test_cancel_all_and_emergency_stop(monkeypatch):
    """전량 취소: 승인 철회 · 예약주문 취소 TR · 발주된 정규 주문 취소 TR, 체결 줄은 대상 외. stop=true 면 정지 + 자동 승인 끔."""
    import app.autoapprove as aa
    import app.broker as br
    from app.models import BrokerOrder

    c, h = _client()
    exec_day = _next_weekday(datetime.now(KST).date())
    pid, aid = _setup(c, h, exec_day)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    c.put(f"/portfolio/{pid}/auto-exec/auto-approve", json={"enabled": True, "market_reserve": True}, headers=h)
    monkeypatch.setattr(aa, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "ok"})
    fake = FakeBroker()
    rec = _rec(_run(exec_day, fake, aid), pid)
    assert rec["approved"] == 3 and rec["reserved"] == 1
    # 발주된 정규 주문·체결된 주문을 흉내 (오늘 실행분)
    with SessionLocal() as s:
        s.add(BrokerOrder(portfolio_id=pid, broker_credential_id=aid, plan_date=datetime.now(KST).date(), line_key="grid1:K200:buy:limit:97000",
                          code="069500", instrument="K200", kind="grid1", side="buy", otype="limit", qty=1, price=97000,
                          mode="auto", status="submitted", order_no="N77", response={"order": {"KRX_FWDG_ORD_ORGNO": "06010"}}))
        s.add(BrokerOrder(portfolio_id=pid, broker_credential_id=aid, plan_date=datetime.now(KST).date(), line_key="tp:K200:sell:limit:105000",
                          code="069500", instrument="K200", kind="tp", side="sell", otype="limit", qty=1, price=105000,
                          mode="auto", status="filled", order_no="N78", filled_qty=1))
        s.commit()
    monkeypatch.setattr(br, "_client", lambda cred: fake)
    r = c.post(f"/portfolio/{pid}/orders/cancel-all", json={"stop": True}, headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["cancelled"] == 5 and j["failed"] == 0 and j["filled_untouched"] is True
    assert fake.cancelled == [("N77", "06010")] and fake.rcancelled and fake.rcancelled[0][0] == "R1"
    assert j["auto_exec"]["paused"] is True and "긴급 정지" in j["auto_exec"]["paused_reason"] and j["auto_exec"]["auto_approve"]["enabled"] is False
    rows = c.get(f"/portfolio/{pid}/orders", headers=h).json()["items"]
    by = {(i["line_key"]): i for i in rows}
    assert all(i["status"] == "cancelled" for i in rows if i["status"] != "filled")
    assert by["tp:K200:sell:limit:105000"]["status"] == "filled"
    assert "승인 철회" in by["grid1:K200:buy:limit:99000"]["message"] and "전량 취소" in by["grid1:K200:buy:limit:97000"]["message"]
    ev = [i for i in c.get("/logs?type=event&level=error", headers=h).json()["items"] if i["kind"] == "order.cancel_all"]
    assert ev and "5건 취소" in ev[0]["text"] and "무인 운영 정지" in ev[0]["text"]
    # 살아 있는 주문이 없으면 0건, 정지 유지
    assert c.post(f"/portfolio/{pid}/orders/cancel-all", json={"stop": False}, headers=h).json()["cancelled"] == 0
