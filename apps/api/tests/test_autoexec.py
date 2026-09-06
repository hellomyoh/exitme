"""무인 실행 (2026-09-06 지시, ADR-008) — 설정 스위치·승인·09:01 실행(갭 취소·한도·실패 정지)·장 마감 확정. DB 필요."""
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
    tok = c.post("/auth/register", json={"email": f"ae{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _setup_portfolio(c, h, plan_date: date, orders: list[dict], gap_exact: float | None, deposit_krw=5_000_000):
    """실전 포트 + 계좌 연결 + 입금 + 그날의 주문표 스냅샷(PortfolioPlan)."""
    from app.models import PortfolioPlan

    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    pid = c.post("/portfolios", json={"name": "무인", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": deposit_krw,
                               "executed_at": (plan_date - timedelta(days=3)).isoformat() + "T15:30:00+09:00"}, headers=h)
    with SessionLocal() as s:
        s.add(PortfolioPlan(portfolio_id=pid, trade_date=plan_date,
                            payload={"regime": "NEUTRAL", "orders": orders, "gap_cancel_below": int(gap_exact) if gap_exact else None,
                                     "gap_cancel_exact": gap_exact}))
        s.commit()
    return pid, acct["id"]


class FakeKis:
    """place_order/fetch_price/fetch_balance/fetch_executions 만 흉내 — 네트워크 없음."""

    def __init__(self, open_px: int, deposit: int, holdings: dict[str, int], fail_orders: int = 0, psbl_cash: int | None = None):
        self.open_px, self.deposit, self.holdings, self.fail_orders = open_px, deposit, holdings, fail_orders
        self.psbl_cash = psbl_cash   # None = 매수가능조회 실패(폴백 경로), 숫자 = KIS 주문가능현금(발주마다 차감)
        self.placed: list[tuple] = []
        self.cancelled: list[str] = []
        self.buyable_calls: list[tuple] = []

    def buyable(self, code, price):
        self.buyable_calls.append((code, price))
        if self.psbl_cash is None:
            raise RuntimeError("KIS error EGW00201 조회 실패")
        qty = self.psbl_cash // int(price)
        return {"cash": self.psbl_cash, "cash_qty": qty, "max_qty": qty, "raw": {}}

    def fetch_price(self, code):
        return {"stck_oprc": str(self.open_px), "stck_prpr": str(self.open_px)}

    def fetch_balance(self):
        return {"holdings": [{"code": k, "name": k, "qty": v, "avg_price": 100000, "buy_amount": 0, "price": 100000, "eval_amount": 0}
                             for k, v in self.holdings.items()], "deposit": self.deposit, "total_eval": 0}

    def place_order(self, code, side, qty, price):
        if self.fail_orders > 0:
            self.fail_orders -= 1
            raise RuntimeError("KIS error 40310000 주문가능금액을 초과하였습니다")
        self.placed.append((code, side, qty, price))
        if side == "buy" and self.psbl_cash is not None:
            self.psbl_cash -= qty * price   # 증거금 묶임 — 다음 매수가능조회에 반영
        return {"order_no": f"N{len(self.placed):04d}", "orgno": "00950", "msg": "주문 전송 완료", "raw": {"ODNO": f"N{len(self.placed):04d}"}}

    def cancel_order(self, order_no, orgno=""):
        self.cancelled.append(order_no)
        return {"msg": "취소 완료", "raw": {}}

    def fetch_executions(self, start, end, only_filled=True):
        class E:  # noqa: D401 — 체결 1건 흉내
            def __init__(self, no, q):
                self.order_no, self.filled_qty = no, q
        return [E("N0001", 3)]  # 첫 주문만 3주 체결


LINES = [
    {"instrument": "K200", "kind": "grid1", "side": "buy", "otype": "limit", "qty": 5, "price": 99000},
    {"instrument": "K200", "kind": "grid2", "side": "buy", "otype": "limit", "qty": 3, "price": 98000},
    {"instrument": "K200", "kind": "tp", "side": "sell", "otype": "limit", "qty": 2, "price": 103000},
    {"instrument": "LEV", "kind": "lev_strat", "side": "buy", "otype": "market", "qty": 4, "price": None},
]


def test_approval_requires_setting_and_limit_lines():
    c, h = _client()
    tomorrow = date.today() + timedelta(days=1)
    pid, _ = _setup_portfolio(c, h, tomorrow, LINES, gap_exact=97500.0)
    # 기본: 둘 다 꺼짐 → 승인 거절
    assert c.get("/settings/auto-exec", headers=h).json() == {"buy": False, "sell": False}
    body = {"date": tomorrow.isoformat(), "lines": [LINES[0]]}
    r = c.post(f"/portfolio/{pid}/orders/approve", json=body, headers=h)
    assert r.status_code == 409 and "무인 매수" in r.json()["detail"]
    # 매수만 켬 → 매수 승인 OK, 매도 줄 포함 시 거절, 시장가 줄 거절
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    assert c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[2]]}, headers=h).status_code == 409
    assert c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[3]]}, headers=h).status_code == 409
    r = c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h).json()
    assert r["approved"] == 2 and all(i["status"] == "approved" and i["mode"] == "auto" for i in r["items"])
    # 같은 줄 재승인은 중복, 계획과 수량이 다르면 불일치
    r2 = c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[0], {**LINES[1], "qty": 9}]}, headers=h).json()
    assert [i["status"] for i in r2["items"]] == ["duplicate", "mismatch"]
    # 주문 목록에 무인 상태·허용 스위치가 실린다
    lst = c.get(f"/portfolio/{pid}/orders?date={tomorrow.isoformat()}", headers=h).json()
    assert lst["auto_exec"]["allowed"] == {"buy": True, "sell": False} and lst["auto_exec"]["paused"] is False
    assert sum(1 for i in lst["items"] if i["status"] == "approved") == 2
    # 승인 철회 = 취소 (KIS 호출 없음)
    oid = next(i["id"] for i in lst["items"] if i["status"] == "approved")
    assert c.post(f"/portfolio/{pid}/orders/{oid}/cancel", headers=h).json()["status"] == "cancelled"


def test_execution_gap_cancel_limits_and_sync(monkeypatch):
    import app.autoexec as ae

    c, h = _client()
    today = date.today()
    pid, _ = _setup_portfolio(c, h, today, LINES, gap_exact=97500.0)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    # 원장에 069500 10주 등록 — 09:01 사전 대조(원장 vs 계좌)를 통과하려면 계좌 보유와 같아야 한다
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 100000,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)
    # 승인은 09:00 이전이어야 한다 → 승인 시각을 이른 시각으로 흉내
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    r = c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": LINES[:3]}, headers=h).json()
    assert r["approved"] == 3

    # ① 갭 발생 (시가 97,000 ≤ 기준 97,500): 그리드 매수 2건 생략, 익절 매도는 발주. 예수금 충분, 보유 10주
    fake = FakeKis(open_px=97000, deposit=2_000_000, holdings={"069500": 10})
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    rec = out["portfolios"][0]
    assert rec["skipped_gap"] == 2 and rec["submitted"] == 1 and rec["failed"] == 0
    assert fake.placed == [("069500", "sell", 2, 103000)]
    lst = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()
    st = {i["kind"]: i for i in lst["items"]}
    assert st["grid1"]["status"] == "skipped_gap" and "갭 취소" in st["grid1"]["message"]
    assert st["tp"]["status"] == "submitted" and st["tp"]["order_no"] == "N0001"
    assert lst["auto_exec"]["last_run"]["gap_hit"] is True and lst["auto_exec"]["last_run"]["open"] == 97000
    # 같은 날 재실행은 막힌다 (DB 마커)
    with SessionLocal() as s:
        out2 = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 3), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    assert out2["portfolios"] == []   # approved 행이 남아 있지 않다
    # 장 마감 확정: 체결조회 3주 → 매도 2주 주문은 filled
    with SessionLocal() as s:
        from app.models import BrokerCredential, BrokerOrder
        rows = s.scalars(ae.select(BrokerOrder).where(BrokerOrder.portfolio_id == pid)).all()
        cred = s.get(BrokerCredential, rows[0].broker_credential_id)
        changed = ae.sync_auto_orders(s, cred, rows, today, now=datetime.combine(today, ae.time(15, 45), tzinfo=KST), client=fake)
        s.commit()
    assert changed == 1
    assert next(i for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"] if i["kind"] == "tp")["status"] == "filled"


def test_execution_no_gap_cash_limit_and_fail_streak_pauses(monkeypatch):
    import app.autoexec as ae

    c, h = _client()
    today = date.today()
    pid, _ = _setup_portfolio(c, h, today, LINES, gap_exact=97500.0)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": LINES[:3]}, headers=h)
    # 갭 없음(시가 100,000) · 예수금 500,000: 얕은 그리드부터 — grid1(5×99,000=495,000) 발주, grid2(+294,000) 는 한도 초과로 생략,
    # 매도(tp)는 보유 0주라 생략 (2차 검증 Fix B: 전부 생략 → 순차 발주)
    fake = FakeKis(open_px=100000, deposit=500_000, holdings={})
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: fake, sleep_fn=lambda _s: None)
    rec = out["portfolios"][0]
    assert rec["submitted"] == 1 and rec["skipped"] == 2 and rec["skipped_gap"] == 0 and fake.placed == [("069500", "buy", 5, 99000)]
    msgs = {i["kind"]: i["message"] for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]}
    assert "예수금 한도(폴백)" in msgs["grid2"] and "잔고 부족" in msgs["tp"]

    # 다음 날: 발주 2건 연속 실패 → 자동 정지, 이후 승인 거절, 다시 켜기로 해제
    c2, h2 = _client()
    pid2, _ = _setup_portfolio(c2, h2, today, LINES, gap_exact=None, deposit_krw=9_000_000)
    c2.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h2)
    c2.post(f"/portfolio/{pid2}/orders/approve", json={"date": today.isoformat(), "lines": LINES[:2]}, headers=h2)
    bad = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, fail_orders=2)
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST), client_factory=lambda cred: bad, sleep_fn=lambda _s: None)
    assert out["portfolios"][0]["failed"] == 2
    view = c2.get(f"/portfolio/{pid2}/auto-exec", headers=h2).json()
    assert view["paused"] is True and "연속 실패" in view["paused_reason"]
    r = c2.post(f"/portfolio/{pid2}/orders/approve", json={"date": (today + timedelta(days=1)).isoformat(), "lines": [LINES[0]]}, headers=h2)
    assert r.status_code == 409 and "정지" in r.json()["detail"]
    assert c2.post(f"/portfolio/{pid2}/auto-exec/resume", headers=h2).json()["paused"] is False


def test_us_portfolio_and_pause_on_reconcile_warning():
    import app.autoexec as ae

    c, h = _client()
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "y" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    us = c.post("/portfolios", json={"name": "us", "market": "US"}, headers=h).json()["id"]
    c.put(f"/portfolio/{us}/broker", json={"credential_id": acct["id"]}, headers=h)
    r = c.post(f"/portfolio/{us}/orders/approve", json={"date": (date.today() + timedelta(days=1)).isoformat(), "lines": [LINES[0]]}, headers=h)
    assert r.status_code == 409 and "국내" in r.json()["detail"]
    # 대조 경고 → 정지
    with SessionLocal() as s:
        from app.models import TradePortfolio
        pf = s.scalar(ae.select(TradePortfolio).where(TradePortfolio.id == us))
        # 부분체결(short)·미이행(missing) 은 정지하지 않고, 계획에 없던 거래(unplanned) 만 정지 (2차 검증 Fix C)
        assert ae.pause_if_reconcile_warns(s, pf, {"date": "2026-09-06", "items": [{"level": "warn", "kind": "short", "text": "계획 8 ≠ 등록 5"}]}) is False
        assert ae.pause_if_reconcile_warns(s, pf, {"date": "2026-09-06", "items": [{"level": "warn", "kind": "unplanned", "text": "레버리지 매수 3주 등록 — 이날 계획에 없던 거래"}]}) is True
        assert ae.pf_auto_state(pf)["paused"] is True and "계획에 없던" in ae.pf_auto_state(pf)["paused_reason"]
        assert ae.pause_if_reconcile_warns(s, pf, {"items": [{"level": "info", "kind": "missing", "text": "ok"}]}) is False
