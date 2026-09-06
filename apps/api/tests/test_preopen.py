"""장 시작 전 예상 시가 갭 취소 (2026-09-06 지시) — 예상체결가·미체결 조회 파싱, 08:57 실행(비갭 유지 / 갭 취소·앱 밖 주문·미매칭·실패 / 설정 꺼짐 / 하루 1회)."""
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

db = pytest.mark.skipif(not DB_UP, reason="database not reachable")
KST = timezone(timedelta(hours=9))


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"po{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


class _Auth:
    env = "prod"
    base_url = "https://example.invalid"

    def headers(self, tr_id, session=None):
        return {}


def test_fetch_expected_and_open_orders_parse():
    """예상체결가(FHKST01010200 output2.antc_cnpr)·정정취소가능주문(TTTC0084R) 파싱, 모의투자는 미체결 조회 거절."""
    from app.services.kis_client import EXPECTED_TR, OPEN_ORDERS_TR, KisError, KisTradingClient

    def fake_get(self, path, tr_id, params):
        if tr_id == EXPECTED_TR:
            return {"output1": {"aspr_acpt_hour": "085700"}, "output2": {"antc_cnpr": "98500", "antc_cnqn": "1200", "aspr_acpt_hour": "085700"}}
        if tr_id == OPEN_ORDERS_TR:
            return {"output": [{"odno": "0000123", "ord_gno_brno": "06010", "pdno": "069500", "prdt_name": "KODEX 200",
                                "sll_buy_dvsn_cd": "02", "ord_qty": "5", "ord_unpr": "99000", "tot_ccld_qty": "0", "psbl_qty": "5", "ord_tmd": "083012"}],
                    "ctx_area_nk100": ""}
        raise AssertionError(tr_id)

    c = KisTradingClient(_Auth(), cano="12345678", acnt_prdt_cd="01")
    c._get = fake_get.__get__(c)  # type: ignore[method-assign]
    c._throttle = lambda: None      # type: ignore[method-assign]
    assert c.fetch_expected("069500")["expected"] == 98_500
    rows = c.list_open_orders()
    assert rows[0]["order_no"] == "0000123" and rows[0]["orgno"] == "06010" and rows[0]["side"] == "buy"
    assert rows[0]["price"] == 99_000 and rows[0]["qty"] == 5 and rows[0]["psbl_qty"] == 5

    class _Vps(_Auth):
        env = "vps"
    with pytest.raises(KisError):
        KisTradingClient(_Vps(), cano="12345678").list_open_orders()


class FakePre:
    """fetch_expected / list_open_orders / cancel_order 만 흉내 — 네트워크 없음."""

    def __init__(self, expected: int | None, open_orders: list[dict], fail_cancel_for: set[str] | None = None):
        self.expected, self.open_orders = expected, list(open_orders)
        self.fail_cancel_for = set(fail_cancel_for or ())
        self.cancelled: list[tuple[str, str]] = []
        self.expected_calls = 0

    def fetch_expected(self, code):
        self.expected_calls += 1
        return {"expected": self.expected or 0, "expected_qty": 100, "time": "085700", "raw": {}}

    def list_open_orders(self):
        return self.open_orders

    def cancel_order(self, order_no, orgno=""):
        if order_no in self.fail_cancel_for:
            raise RuntimeError("KIS error 40580000 취소 불가 (이미 체결)")
        self.cancelled.append((order_no, orgno))
        return {"msg": "취소 완료", "raw": {"ODNO": order_no}}


def _open(order_no, price, qty=5, code="069500", side="buy", orgno="06010"):
    return {"order_no": order_no, "orgno": orgno, "code": code, "name": code, "side": side, "qty": qty, "price": price,
            "filled_qty": 0, "psbl_qty": qty, "time": "083000", "raw": {}}


def _setup(c, h, today: date, gap_exact: float | None = 98_700.0, reserved_prices=(99_000, 98_000), env="prod", grid=True):
    """실전 포트 + 계좌 연결 + 오늘 계획(그리드 2줄·익절 1줄) + 접수된 예약주문 행."""
    from app.broker import line_key
    from app.models import BrokerOrder, PortfolioPlan

    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01", "env": env}, headers=h).json()
    pid = c.post("/portfolios", json={"name": "사전갭", "market": "KR", "code_200": "069500", "credential_id": acct["id"]}, headers=h).json()["id"]
    orders = ([{"instrument": "K200", "kind": "grid1", "side": "buy", "otype": "limit", "qty": 5, "price": 99_000},
               {"instrument": "K200", "kind": "grid2", "side": "buy", "otype": "limit", "qty": 3, "price": 98_000},
               {"instrument": "K200", "kind": "grid3", "side": "buy", "otype": "limit", "qty": 2, "price": 97_000}] if grid else []) + \
             [{"instrument": "K200", "kind": "tp", "side": "sell", "otype": "limit", "qty": 2, "price": 103_000}]
    with SessionLocal() as s:
        s.add(PortfolioPlan(portfolio_id=pid, trade_date=today,
                            payload={"regime": "NEUTRAL", "orders": orders, "gap_cancel_exact": gap_exact,
                                     "gap_cancel_below": int(gap_exact) if gap_exact else None}))
        for o in orders:
            if o["side"] == "buy" and o["price"] in reserved_prices:
                s.add(BrokerOrder(portfolio_id=pid, broker_credential_id=acct["id"], plan_date=today, line_key=line_key(o),
                                  code="069500", instrument="K200", kind=o["kind"], side="buy", otype="limit", qty=o["qty"],
                                  price=o["price"], rsvn_ord_seq=str(100 + o["price"] // 1000), status="reserved", mode="reserve"))
        s.commit()
    return pid, acct["id"]


def _run(fake, today, acct_id):
    """실행 — 이 테스트의 계좌만 가짜 클라이언트를 받고, DB 에 남은 다른 테스트의 오늘 계획은 '예상체결가 없음' 으로 지나간다."""
    from app.preopen import run_preopen_cancel

    idle = FakePre(expected=None, open_orders=[])
    with SessionLocal() as s:
        return run_preopen_cancel(s, now=datetime.combine(today, datetime.min.time(), tzinfo=KST).replace(hour=8, minute=57),
                                  client_factory=lambda cred: fake if cred.id == acct_id else idle, sleep_fn=lambda _s: None)


def _rec(out, pid):
    return next(r for r in out["portfolios"] if r["portfolio_id"] == pid)


@db
def test_preopen_no_gap_keeps_orders():
    """예상체결가 > 기준 → 아무것도 취소하지 않고 '유지' 기록만. 주문표 응답 preopen.last_run, 로그 이벤트(정상)."""
    c, h = _client()
    today = date.today()
    pid, aid = _setup(c, h, today)
    fake = FakePre(expected=99_500, open_orders=[_open("A1", 99_000), _open("A2", 98_000, 3)])
    rec = _rec(_run(fake, today, aid), pid)
    assert rec["gap_hit"] is False and rec["cancelled"] == 0 and fake.cancelled == [] and fake.expected_calls == 1
    bo = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()
    assert all(i["status"] == "reserved" for i in bo["items"])
    lr = bo["preopen"]["last_run"]
    assert lr["date"] == today.isoformat() and lr["expected"] == 99_500 and lr["gap_hit"] is False and lr["gap_exact"] == 98_700.0
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "preopen.run"]
    assert len(ev) == 1 and ev[0]["level"] == "info" and "그리드 매수 유지" in ev[0]["text"]


@db
def test_preopen_gap_cancels_tracked_untracked_reports_unmatched_and_runs_once():
    """예상체결가 ≤ 기준 → 200 ETF 매수·그리드 가격과 같은 미체결만 취소(앱 예약주문 + HTS 직접 주문), 매도·다른 종목은 건드리지 않음.
    미체결 목록에 없는 앱 예약주문은 '취소 불가'로 기록. 같은 날 두 번째 실행은 아무것도 하지 않는다."""
    c, h = _client()
    today = date.today()
    pid, aid = _setup(c, h, today, reserved_prices=(99_000, 98_000, 97_000))
    fake = FakePre(expected=98_500, open_orders=[
        _open("A1", 99_000, 5), _open("A2", 98_000, 3), _open("A3", 98_000, 2),            # A3 = 앱 밖(HTS) 주문, 같은 그리드 가격
        _open("B1", 103_000, 2, side="sell"), _open("C1", 99_000, 1, code="005930"),   # 대상 외
        _open("D1", 98_500, 1),                                                          # 그리드 가격과 다른 매수 — 대상 외
    ])
    rec = _rec(_run(fake, today, aid), pid)
    assert rec["gap_hit"] is True and rec["cancelled"] == 3 and rec["untracked"] == 1 and rec["unmatched"] == 1 and rec["failed"] == 0
    assert sorted(no for no, _ in fake.cancelled) == ["A1", "A2", "A3"] and fake.cancelled[0][1] == "06010"
    bo = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()
    st = {i["kind"]: i for i in bo["items"]}
    assert st["grid1"]["status"] == "gap_cancelled" and st["grid1"]["status_ko"] == "갭 취소됨(예상 시가)" and st["grid1"]["order_no"] == "A1"
    assert st["grid2"]["status"] == "gap_cancelled" and "98,500원 ≤ 기준 98,700원" in st["grid2"]["message"]
    assert st["grid3"]["status"] == "reserved" and "찾지 못해" in st["grid3"]["message"]
    lr = bo["preopen"]["last_run"]
    assert lr["gap_hit"] is True and lr["cancelled"] == 3 and lr["untracked"] == 1 and lr["unmatched"] == 1
    logs = c.get("/logs?level=warn&type=event", headers=h).json()["items"]
    kinds = [i["kind"] for i in logs]
    assert kinds.count("preopen.cancel") == 3 and kinds.count("preopen.unmatched") == 1 and kinds.count("preopen.run") == 1
    assert any("앱 밖에서 낸 주문" in i["text"] for i in logs if i["kind"] == "preopen.cancel")
    # 하루 1회 — 두 번째 실행은 기록만 하고 취소하지 않는다
    rec2 = _rec(_run(fake, today, aid), pid)
    assert rec2["note"] == "already-ran" and len(fake.cancelled) == 3


@db
def test_preopen_cancel_failure_setting_off_and_no_grid():
    """취소 실패는 줄 메시지·오류 이벤트로 남고 실행 요약이 오류 수준. 설정을 끄면 조회조차 하지 않는다. 그리드가 없는 계획은 건너뛴다."""
    c, h = _client()
    today = date.today()
    pid, aid = _setup(c, h, today, reserved_prices=(99_000,))
    fake = FakePre(expected=98_000, open_orders=[_open("A1", 99_000)], fail_cancel_for={"A1"})
    rec = _rec(_run(fake, today, aid), pid)
    assert rec["gap_hit"] is True and rec["cancelled"] == 0 and rec["failed"] == 1
    bo = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()
    g1 = next(i for i in bo["items"] if i["kind"] == "grid1")
    assert g1["status"] == "reserved" and "취소 실패" in g1["message"]
    errs = [i for i in c.get("/logs?level=error", headers=h).json()["items"]]
    assert any(i["kind"] == "preopen.cancel_failed" for i in errs) and any(i["kind"] == "preopen.run" for i in errs)

    # 설정 꺼짐 — 다른 포트(다른 사용자)로 확인
    c2, h2 = _client()
    pid2, aid2 = _setup(c2, h2, today)
    c2.put("/settings/auto-exec", json={"buy": False, "sell": False, "preopen_cancel": False}, headers=h2)
    assert c2.get("/settings/auto-exec", headers=h2).json()["preopen_cancel"] is False
    fake2 = FakePre(expected=90_000, open_orders=[_open("Z1", 99_000)])
    rec2 = _rec(_run(fake2, today, aid2), pid2)
    assert rec2["note"] == "설정 꺼짐" and fake2.expected_calls == 0 and fake2.cancelled == []

    # 그리드 매수 없는 계획 — 예상체결가를 읽지 않고 건너뜀
    c3, h3 = _client()
    pid3, aid3 = _setup(c3, h3, today, grid=False, reserved_prices=())
    fake3 = FakePre(expected=90_000, open_orders=[])
    rec3 = _rec(_run(fake3, today, aid3), pid3)
    assert rec3["note"] == "그리드 매수 없음" and fake3.expected_calls == 0
