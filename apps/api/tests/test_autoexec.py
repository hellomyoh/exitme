"""무인 매매 단일 실행 (ADR-009, 2026-09-08 지시) — 계좌 플래그·상태 표기·09:01 계산→시가→갭→잔고→상한→발주·사용자 취소·설정 해제 취소·감시·장 마감 확정. DB 필요."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

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


def _setup_portfolio(c, h, plan_date: date, deposit_krw=5_000_000):
    """실전 포트 + 계좌 연결 + 입금. 주문표는 실행기에 plan_fn 으로 주입한다(계산 자체는 test_signals 가 검증)."""
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    pid = c.post("/portfolios", json={"name": "무인", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": deposit_krw,
                               "executed_at": (plan_date - timedelta(days=3)).isoformat() + "T15:30:00+09:00"}, headers=h)
    return pid, acct["id"]


class FakeKis:
    """place_order/fetch_price/fetch_balance/buyable/cancel_order/fetch_executions 만 흉내 — 네트워크 없음."""

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
        self.placed.append((code, side, qty, price))   # price None = 시장가
        if side == "buy" and self.psbl_cash is not None and price:
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
IDLE = lambda: FakeKis(open_px=0, deposit=0, holdings={})  # noqa: E731 — 다른 계좌용(호출돼도 발주 없음)


def _plan(lines, exec_day: date, gap_exact=None, equity=100_000_000):
    """실행기에 주입하는 주문표 — _portfolio_orders(force_freeze) 대신. exec_day 가 오늘이 아니면 실행기가 '기준일 불일치'로 발주하지 않는다."""
    def fn(session, pf, now):
        return {"exec_day": exec_day.isoformat(), "orders": [dict(o) for o in lines], "gap_cancel_exact": gap_exact,
                "gap_cancel_below": int(gap_exact) if gap_exact else None,
                "account": {"equity": equity, "cash": 0, "qty_200": 0, "qty_lev": 0}}
    return fn


def _run(fake, aid, today: date, lines, gap_exact=None, equity=100_000_000, at=(9, 1), exec_day=None, trigger="beat"):
    import app.autoexec as ae

    with SessionLocal() as s:
        fn = ae.run_watchdog if trigger == "watchdog" else ae.run_auto_execution
        kw = {} if trigger == "watchdog" else {"trigger": trigger}
        out = fn(s, now=datetime.combine(today, time(*at), tzinfo=KST), client_factory=lambda cred: fake if cred.id == aid else IDLE(),
                 sleep_fn=lambda _s: None, plan_fn=_plan(lines, exec_day or today, gap_exact, equity), only_credential_ids={aid}, **kw)
    assert len(out["portfolios"]) == 1
    return out["portfolios"][0], out


def _orders(c, h, pid, day: date) -> dict:
    lst = c.get(f"/portfolio/{pid}/orders?date={day.isoformat()}", headers=h).json()
    return {i["kind"]: i for i in lst["items"]}, lst["auto_exec"]


def test_settings_flags_default_bulk_and_state_off_waiting():
    """플래그 기본값·일괄 저장, 상태 한 줄(off → waiting), 승인·전량 취소 엔드포인트 제거."""
    c, h = _client()
    tomorrow = datetime.now(KST).date() + timedelta(days=1)
    pid, aid = _setup_portfolio(c, h, tomorrow)
    assert c.get("/settings/auto-exec", headers=h).json()["default"] == {"buy": False, "sell": False, "daily_buy_cap_pct": 0.0}   # 기본 0 = 없음 (2026-09-08)
    v = c.get(f"/portfolio/{pid}/auto-exec?date={tomorrow.isoformat()}", headers=h).json()
    assert v["state"]["code"] == "off" and "수동 모드" in v["state"]["label"] and "위탁" in v["state"]["label"] and v["exec_day"] == tomorrow.isoformat()
    g = c.put("/settings/auto-exec", json={"buy": True, "sell": False, "daily_buy_cap_pct": 15}, headers=h).json()
    assert all(a["auto_exec"] == {"buy": True, "sell": False, "daily_buy_cap_pct": 15.0} for a in g["accounts"]) and g["cancelled"] == 0
    v = c.get(f"/portfolio/{pid}/auto-exec?date={tomorrow.isoformat()}", headers=h).json()
    assert v["state"]["code"] == "waiting" and "09:01" in v["state"]["label"] and tomorrow.isoformat() in v["state"]["label"] and "매수" in v["state"]["label"]
    _, ae_view = _orders(c, h, pid, tomorrow)
    assert ae_view["state"]["code"] == "waiting" and ae_view["allowed"]["buy"] is True
    # 2단계(승인)·전량 취소·완전 무인 엔드포인트는 사라졌다 (ADR-009)
    assert c.post(f"/portfolio/{pid}/orders/approve", json={"date": tomorrow.isoformat(), "lines": [LINES[0]]}, headers=h).status_code in (404, 405)
    assert c.post(f"/portfolio/{pid}/orders/cancel-all", json={}, headers=h).status_code in (404, 405)
    assert c.put(f"/portfolio/{pid}/auto-exec/auto-approve", json={"enabled": True}, headers=h).status_code in (404, 405)


def test_single_execution_gap_cancel_market_line_state_and_sync(monkeypatch):
    """09:01 한 번에: 갭 발생 → 그리드 매수 생략, 익절 매도(지정가)·레버리지 진입(시장가) 발주. 상태 'ran', 재실행·감시 중복 없음, 장 마감 확정."""
    import app.autoexec as ae

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today)
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    # 원장에 069500 10주 — 09:01 사전 대조(원장 vs 계좌)를 통과하려면 계좌 보유와 같아야 한다
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 100000,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)
    monkeypatch.setattr(ae, "_latest_close", lambda s, code: 20_000)   # 시장가 줄의 상한 환산용 최근 종가
    fake = FakeKis(open_px=97000, deposit=2_000_000, holdings={"069500": 10}, psbl_cash=5_000_000)
    rec, _ = _run(fake, aid, today, LINES, gap_exact=97500.0)
    assert rec["exec_day"] == today.isoformat() and rec["skipped_gap"] == 2 and rec["submitted"] == 2 and rec["failed"] == 0 and rec["skipped"] == 0
    assert fake.placed == [("069500", "sell", 2, 103000), ("122630", "buy", 4, None)]   # 매도 먼저, 시장가 매수 포함(price None)
    st, view = _orders(c, h, pid, today)
    assert st["grid1"]["status"] == "skipped_gap" and "갭 취소" in st["grid1"]["message"] and st["grid1"]["mode"] == "auto"
    assert st["tp"]["status"] == "submitted" and st["tp"]["order_no"] == "N0001"
    assert st["lev_strat"]["status"] == "submitted" and st["lev_strat"]["otype"] == "market" and st["lev_strat"]["price"] is None
    assert view["last_run"]["gap_hit"] is True and view["last_run"]["open"] == 97000 and view["last_run"]["note"] == "ran"
    assert view["state"]["code"] == "ran" and "발주 2건" in view["state"]["label"] and "갭 취소 생략 2건" in view["state"]["label"] and "97,000" in view["state"]["detail"]
    # 같은 날 재실행(09:03)·09:15 감시 모두 중복 발주 없음 (DB 마커)
    rec2, _ = _run(fake, aid, today, LINES, gap_exact=97500.0, at=(9, 3))
    assert rec2["error"] == "already-ran" and len(fake.placed) == 2
    rec3, out3 = _run(fake, aid, today, LINES, gap_exact=97500.0, at=(9, 15), trigger="watchdog")
    assert rec3["error"] == "already-ran" and out3["late"] == [] and len(fake.placed) == 2
    # 장 마감 확정: 체결조회 3주 → 매도 2주 주문은 filled
    with SessionLocal() as s:
        from app.models import BrokerCredential, BrokerOrder
        rows = s.scalars(ae.select(BrokerOrder).where(BrokerOrder.portfolio_id == pid)).all()
        cred = s.get(BrokerCredential, rows[0].broker_credential_id)
        changed = ae.sync_auto_orders(s, cred, rows, today, now=datetime.combine(today, time(15, 45), tzinfo=KST), client=fake)
        s.commit()
    assert changed == 2   # tp filled, lev_strat(체결 없음) → 장 마감 뒤 unfilled
    st, _ = _orders(c, h, pid, today)
    assert st["tp"]["status"] == "filled" and st["lev_strat"]["status"] == "unfilled"


def test_execution_clips_to_cap_and_buyable_and_pauses_on_fail_streak():
    """상한·주문가능 수량은 정지가 아니라 축소(0 이면 생략). 꺼진 방향은 '수동 처리'. 연속 실패 2회 → 정지·다시 켜기."""
    import app.autoexec as ae

    # A) 하루 매수 상한: 총자산 2,000,000 × 20% = 400,000 → grid1 5×99,000 은 4주(396,000)로 축소, grid2 는 0 → 생략. 매도 꺼짐 → 익절 수동
    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True, "daily_buy_cap_pct": 20}, headers=h)   # 상한은 기본 0 — 이 테스트만 20%
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec, _ = _run(fake, aid, today, LINES[:3], equity=2_000_000)
    assert rec["submitted"] == 1 and rec["skipped"] == 2 and rec["clipped"] == 1 and fake.placed == [("069500", "buy", 4, 99000)]
    st, view = _orders(c, h, pid, today)
    assert st["grid1"]["status"] == "submitted" and "상한" in st["grid1"]["message"] and "5→4주" in st["grid1"]["message"]
    assert st["grid2"]["status"] == "skipped" and "하루 매수 상한" in st["grid2"]["message"]
    assert st["tp"]["status"] == "skipped" and st["tp"]["message"] == "무인 매도 꺼짐 — 수동 처리"
    assert "축소 1건" in view["state"]["label"]

    # B) 매수가능 수량: 상한 없음(0), 예수금 총액 0 이지만 KIS 주문가능현금 600,000 → grid1 5주(495,000) 발주 뒤 잔여 105,000 → grid2 3→1주 축소 발주
    c2, h2 = _client()
    pid2, aid2 = _setup_portfolio(c2, h2, today)
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"buy": True, "daily_buy_cap_pct": 0}, headers=h2)
    fake2 = FakeKis(open_px=100000, deposit=0, holdings={}, psbl_cash=600_000)
    rec2, _ = _run(fake2, aid2, today, LINES[:2])
    assert rec2["submitted"] == 2 and rec2["clipped"] == 1 and fake2.placed == [("069500", "buy", 5, 99000), ("069500", "buy", 1, 98000)]
    assert fake2.buyable_calls == [("069500", 99000), ("069500", 98000)]          # 얕은 단부터, 발주 직전마다 조회
    st2, _ = _orders(c2, h2, pid2, today)
    assert "주문가능 수량에 맞춰 3→1주" in st2["grid2"]["message"]

    # C) 발주 2건 연속 실패 → 자동 정지(상태 paused), 다시 켜기로 해제
    c3, h3 = _client()
    pid3, aid3 = _setup_portfolio(c3, h3, today, deposit_krw=9_000_000)
    c3.put(f"/settings/auto-exec/accounts/{aid3}", json={"buy": True}, headers=h3)
    bad = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, fail_orders=2, psbl_cash=9_000_000)
    rec3, _ = _run(bad, aid3, today, LINES[:2])
    assert rec3["failed"] == 2
    view = c3.get(f"/portfolio/{pid3}/auto-exec?date={today.isoformat()}", headers=h3).json()
    assert view["paused"] is True and "연속 실패" in view["paused_reason"] and view["state"]["code"] == "paused"
    assert c3.post(f"/portfolio/{pid3}/auto-exec/resume", headers=h3).json()["paused"] is False
    del ae


def test_manual_mode_stale_plan_user_skip_and_flag_off_cancel(monkeypatch):
    """플래그 꺼짐 = 수동 모드(주문표만 동결, 기록 없음) · 기준일 불일치 = 발주 안 함 + 오류 로그 · 사용자 취소(09:00 전 건너뜀 / 09:01 후 주문 취소) ·
    설정 해제 = 살아 있는 무인 주문 즉시 취소."""
    import app.autoexec as ae

    today = datetime.now(KST).date()
    # 수동 모드 — BrokerOrder 없음, 활동 로그 없음(잡음 방지), 상태 off
    c, h = _client()
    pid, aid = _setup_portfolio(c, h, today)
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec, _ = _run(fake, aid, today, LINES[:2])
    assert rec["note"] == "manual" and fake.placed == []
    st, view = _orders(c, h, pid, today)
    assert st == {} and view["state"]["code"] == "off" and view["last_run"]["note"] == "manual"
    assert not any(i["kind"] == "autoexec.run" for i in c.get("/logs?type=event", headers=h).json()["items"])

    # 기준일 불일치 — 어제 일봉이 없어 계산된 실행일이 어제면 발주하지 않고 오류 로그
    c2, h2 = _client()
    pid2, aid2 = _setup_portfolio(c2, h2, today)
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"buy": True}, headers=h2)
    rec2, _ = _run(fake, aid2, today, LINES[:2], exec_day=today - timedelta(days=1))
    assert rec2["note"] == "stale_plan" and "일봉 누락" in rec2["reason"] and fake.placed == []
    st2, view2 = _orders(c2, h2, pid2, today)
    assert st2 == {} and view2["state"]["label"].startswith("발주 안 함")
    ev = [i for i in c2.get("/logs?type=event", headers=h2).json()["items"] if i["kind"] == "autoexec.run"]
    assert ev and ev[0]["level"] == "error" and "발주 안 함" in ev[0]["text"]

    # 사용자 취소(09:00 전): 되돌리기 가능, 취소 상태면 그날 실행기는 발주하지 않는다
    c3, h3 = _client()
    pid3, aid3 = _setup_portfolio(c3, h3, today)
    c3.put(f"/settings/auto-exec/accounts/{aid3}", json={"buy": True}, headers=h3)
    monkeypatch.setattr("app.signals.freeze_at", lambda d: datetime.combine(d, time(23, 59), tzinfo=KST))   # 아직 09:00 전으로 간주
    r = c3.post(f"/portfolio/{pid3}/auto-exec/skip", json={"date": today.isoformat()}, headers=h3).json()
    assert r["state"]["code"] == "skipped_user" and r["cancelled"] == 0 and "되돌리기" in r["state"]["detail"]
    assert c3.post(f"/portfolio/{pid3}/auto-exec/unskip", headers=h3).json()["state"]["code"] == "waiting"
    assert c3.post(f"/portfolio/{pid3}/auto-exec/skip", json={"date": today.isoformat()}, headers=h3).status_code == 200
    rec3, _ = _run(fake, aid3, today, LINES[:2])
    assert rec3["note"] == "skipped_user" and fake.placed == []
    st3, view3 = _orders(c3, h3, pid3, today)
    assert st3 == {} and view3["state"]["code"] == "skipped_user"
    assert c3.post(f"/portfolio/{pid3}/auto-exec/skip", json={"date": (today - timedelta(days=1)).isoformat()}, headers=h3).status_code == 409

    # 사용자 취소(09:01 후): 살아 있는 무인 주문을 증권사에서 취소
    c4, h4 = _client()
    pid4, aid4 = _setup_portfolio(c4, h4, today, deposit_krw=9_000_000)
    c4.put(f"/settings/auto-exec/accounts/{aid4}", json={"buy": True}, headers=h4)
    fake4 = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec4, _ = _run(fake4, aid4, today, LINES[:2])
    assert rec4["submitted"] == 2
    monkeypatch.setattr("app.signals.freeze_at", lambda d: datetime.combine(d, time(0, 0), tzinfo=KST))    # 09:00 이 지났다고 간주
    monkeypatch.setattr(ae, "_client", lambda cred: fake4)
    r4 = c4.post(f"/portfolio/{pid4}/auto-exec/skip", json={"date": today.isoformat()}, headers=h4).json()
    assert r4["cancelled"] == 2 and r4["failed"] == 0 and fake4.cancelled == ["N0001", "N0002"] and r4["state"]["code"] == "skipped_user"
    st4, _ = _orders(c4, h4, pid4, today)
    assert all(st4[k]["status"] == "cancelled" and "사용자 취소" in st4[k]["message"] for k in ("grid1", "grid2"))
    assert c4.post(f"/portfolio/{pid4}/auto-exec/unskip", headers=h4).status_code == 409   # 09:00 뒤엔 되돌리기 불가

    # 설정 해제 → 그 방향의 오늘 무인 주문 즉시 취소 (사용자 결정 2026-09-08)
    c5, h5 = _client()
    pid5, aid5 = _setup_portfolio(c5, h5, today, deposit_krw=9_000_000)
    c5.put(f"/settings/auto-exec/accounts/{aid5}", json={"buy": True, "sell": True}, headers=h5)
    fake5 = FakeKis(open_px=100000, deposit=9_000_000, holdings={"069500": 0}, psbl_cash=9_000_000)
    rec5, _ = _run(fake5, aid5, today, LINES[:2])
    assert rec5["submitted"] == 2
    monkeypatch.setattr(ae, "_client", lambda cred: fake5)
    r5 = c5.put(f"/settings/auto-exec/accounts/{aid5}", json={"sell": False}, headers=h5).json()   # 매도만 끔 → 매수 주문은 그대로
    assert r5["cancelled"] == 0 and fake5.cancelled == []
    r5 = c5.put(f"/settings/auto-exec/accounts/{aid5}", json={"buy": False}, headers=h5).json()
    assert r5["cancelled"] == 2 and fake5.cancelled == ["N0001", "N0002"]
    st5, view5 = _orders(c5, h5, pid5, today)
    assert all(st5[k]["status"] == "cancelled" and "무인 매수 해제" in st5[k]["message"] for k in ("grid1", "grid2"))
    assert view5["state"]["code"] == "off"
    ev5 = [i for i in c5.get("/logs?type=event", headers=h5).json()["items"] if i["kind"] in ("autoexec.cancel", "autoexec.account_setting")]
    assert any(i["kind"] == "autoexec.cancel" and "2건 취소" in i["text"] for i in ev5)
    assert any(i["kind"] == "autoexec.account_setting" and "살아 있는 무인 주문 2건 취소" in i["text"] for i in ev5)


def test_watchdog_runs_late_when_0901_missing():
    """09:15 감시 — 09:01 실행 기록이 없으면 지연 실행하고(trigger=watchdog) 로그·상태에 드러낸다 (2026-09-08 사고 재발 방지)."""
    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True}, headers=h)
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec, out = _run(fake, aid, today, LINES[:2], at=(9, 15), trigger="watchdog")
    assert out["late"] == [pid] and rec["submitted"] == 2 and "heartbeat_age" in out
    _, view = _orders(c, h, pid, today)
    assert view["last_run"]["trigger"] == "watchdog" and view["state"]["code"] == "ran" and "지연 실행" in view["state"]["detail"]
    ev = next(i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.run")
    assert ev["level"] == "warn" and "지연 실행" in ev["text"] and "발주 2건" in ev["text"]


def test_us_portfolio_and_pause_on_reconcile_warning():
    import app.autoexec as ae

    c, h = _client()
    c.put("/settings/auto-exec", json={"buy": True, "sell": True}, headers=h)
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "y" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    us = c.post("/portfolios", json={"name": "us", "market": "US"}, headers=h).json()["id"]
    c.put(f"/portfolio/{us}/broker", json={"credential_id": acct["id"]}, headers=h)
    v = c.get(f"/portfolio/{us}/auto-exec", headers=h).json()
    assert v["state"]["code"] == "off" and "국내" in v["state"]["label"]
    # 대조 경고 → 정지
    with SessionLocal() as s:
        from app.models import TradePortfolio
        pf = s.scalar(ae.select(TradePortfolio).where(TradePortfolio.id == us))
        # 부분체결(short)·미이행(missing) 은 정지하지 않고, 계획에 없던 거래(unplanned) 만 정지 (2차 검증 Fix C)
        assert ae.pause_if_reconcile_warns(s, pf, {"date": "2026-09-06", "items": [{"level": "warn", "kind": "short", "text": "계획 8 ≠ 등록 5"}]}) is False
        assert ae.pause_if_reconcile_warns(s, pf, {"date": "2026-09-06", "items": [{"level": "warn", "kind": "unplanned", "text": "레버리지 매수 3주 등록 — 이날 계획에 없던 거래"}]}) is True
        assert ae.pf_auto_state(pf)["paused"] is True and "계획에 없던" in ae.pf_auto_state(pf)["paused_reason"]
        assert ae.pause_if_reconcile_warns(s, pf, {"items": [{"level": "info", "kind": "missing", "text": "ok"}]}) is False


def test_boot_line_is_gap_filtered_and_placed_first():
    """ADR-010: 초기 진입(boot) 지정가 — 갭 출발이면 그리드와 함께 생략, 아니면 종가 지정가(가장 얕음)라 그리드보다 먼저 발주."""
    today = datetime.now(KST).date()
    boot = {"instrument": "K200", "kind": "boot", "side": "buy", "otype": "limit", "qty": 2, "price": 100500}
    c, h = _client()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True}, headers=h)
    fake = FakeKis(open_px=97000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec, _ = _run(fake, aid, today, [boot, LINES[0]], gap_exact=97500.0)
    assert rec["skipped_gap"] == 2 and rec["submitted"] == 0 and fake.placed == []
    st, _ = _orders(c, h, pid, today)
    assert st["boot"]["status"] == "skipped_gap" and "초기 진입" in st["boot"]["message"]
    c2, h2 = _client()
    pid2, aid2 = _setup_portfolio(c2, h2, today, deposit_krw=9_000_000)
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"buy": True}, headers=h2)
    fake2 = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec2, _ = _run(fake2, aid2, today, [LINES[0], boot], gap_exact=97500.0)
    assert rec2["submitted"] == 2 and fake2.placed == [("069500", "buy", 2, 100500), ("069500", "buy", 5, 99000)]


def test_intraday_retry_replaces_failed_and_skipped_lines(monkeypatch):
    """장중 재시도 (2026-09-09 지시 "API 실패 시 취소 대신 재시도"): 09:01 결과가 실패·생략인 줄만 새 행으로 같은 절차로 다시 발주.
    갭 생략·꺼진 방향(켜면 대상)·발주된 줄은 대상 외. 장중 밖·정지·대상 없음은 409. 이전 행은 기록으로 남는다."""
    from fastapi import HTTPException

    import app.autoexec as ae
    from app.models import TradePortfolio

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True, "sell": True}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 100000,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)   # 원장 = 계좌
    monkeypatch.setattr(ae, "_latest_close", lambda s, code: 20_000)
    # 09:01 — 첫 발주(익절 매도)가 KIS 오류로 실패, 레버리지·grid1 발주, grid2 는 주문가능현금 부족(65,000 < 98,000)으로 생략
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={"069500": 10}, fail_orders=1, psbl_cash=560_000)
    rec, _ = _run(fake, aid, today, LINES)
    assert rec["submitted"] == 2 and rec["failed"] == 1 and rec["skipped"] == 1
    assert fake.placed == [("122630", "buy", 4, None), ("069500", "buy", 5, 99000)]
    st, view = _orders(c, h, pid, today)
    assert st["tp"]["status"] == "failed" and st["grid2"]["status"] == "skipped" and "주문가능 수량 부족" in st["grid2"]["message"]
    assert view["state"]["code"] == "ran" and view["retryable"] == 2 and view["paused"] is False

    def retry(at, fk=fake):
        with SessionLocal() as s:
            pf = s.get(TradePortfolio, pid)
            out = ae.retry_auto_exec(s, pf, now=datetime.combine(today, time(*at), tzinfo=KST), client_factory=lambda cred: fk,
                                     sleep_fn=lambda _s: None, plan_fn=_plan(LINES, today))
            s.commit()
        return out

    # 장중 밖(15:30) → 409
    with pytest.raises(HTTPException) as ei:
        retry((15, 30))
    assert ei.value.status_code == 409 and "장중" in ei.value.detail
    # 10:30 재시도 — 사용자가 입금해 주문가능현금 2,000,000. 실패한 익절 매도 → 생략된 grid2 순(매도 먼저), 새 행 2개, 이전 행 유지
    fake.psbl_cash = 2_000_000
    res = retry((10, 30))
    assert res["n"] == 2 and res["submitted"] == 2 and res["failed"] == 0 and res["skipped"] == 0
    assert fake.placed[2:] == [("069500", "sell", 2, 103000), ("069500", "buy", 3, 98000)]
    items = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]
    tp_rows = [i for i in items if i["kind"] == "tp"]
    assert [r["status"] for r in tp_rows] == ["failed", "submitted"] and tp_rows[1]["retry_of"] == tp_rows[0]["id"] and tp_rows[0]["retry_of"] is None
    st, view = _orders(c, h, pid, today)
    assert st["tp"]["status"] == "submitted" and st["tp"]["order_no"] == "N0003" and st["grid2"]["status"] == "submitted"
    assert st["grid1"]["order_no"] == "N0002"                                          # 발주된 줄은 건드리지 않는다
    assert view["retryable"] == 0 and view["last_run"]["retry"]["submitted"] == 2 and view["last_run"]["failed"] == 1   # 09:01 요약은 그대로
    assert view["state"]["code"] == "ran" and "재시도 10:30 — 발주 2건" in view["state"]["detail"]
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.retry"]
    assert ev and "대상 2줄" in ev[0]["text"] and "발주 2건" in ev[0]["text"] and ev[0]["level"] == "info"
    # 더 이상 대상 없음 → 409, 엔드포인트도 409(시각과 무관)
    with pytest.raises(HTTPException) as ei2:
        retry((10, 35))
    assert ei2.value.status_code == 409 and "재시도할 줄이 없습니다" in ei2.value.detail
    assert c.post(f"/portfolio/{pid}/auto-exec/retry", headers=h).status_code == 409

    # 갭 생략은 대상 외, 꺼진 방향은 켜면 대상 — 매수만 켠 계좌, 갭 발생: grid1·2 skipped_gap, 익절은 '꺼짐 — 수동 처리'
    c2, h2 = _client()
    pid2, aid2 = _setup_portfolio(c2, h2, today, deposit_krw=9_000_000)
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"buy": True}, headers=h2)
    c2.post("/positions", json={"portfolio_id": pid2, "kind": "buy", "code": "069500", "qty": 10, "price": 100000,
                                "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h2)
    fake2 = FakeKis(open_px=97000, deposit=9_000_000, holdings={"069500": 10}, psbl_cash=9_000_000)
    rec2, _ = _run(fake2, aid2, today, LINES[:3], gap_exact=97500.0)
    assert rec2["skipped_gap"] == 2 and rec2["skipped"] == 1 and fake2.placed == []
    assert c2.get(f"/portfolio/{pid2}/auto-exec?date={today.isoformat()}", headers=h2).json()["retryable"] == 0
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"sell": True}, headers=h2)
    assert c2.get(f"/portfolio/{pid2}/auto-exec?date={today.isoformat()}", headers=h2).json()["retryable"] == 1
    with SessionLocal() as s:
        pf2 = s.get(TradePortfolio, pid2)
        res2 = ae.retry_auto_exec(s, pf2, now=datetime.combine(today, time(10, 0), tzinfo=KST), client_factory=lambda cred: fake2,
                                  sleep_fn=lambda _s: None, plan_fn=_plan(LINES[:3], today, 97500.0))
        s.commit()
    assert res2["n"] == 1 and res2["submitted"] == 1 and res2["gap_hit"] is True and fake2.placed == [("069500", "sell", 2, 103000)]
    st2, _ = _orders(c2, h2, pid2, today)
    assert st2["grid1"]["status"] == "skipped_gap" and st2["tp"]["status"] == "submitted"
    # 정지 상태 → 409
    with SessionLocal() as s:
        pf2 = s.get(TradePortfolio, pid2)
        ae.pause_portfolio(pf2, "테스트 정지")
        s.commit()
        with pytest.raises(HTTPException) as ei3:
            ae.retry_auto_exec(s, pf2, now=datetime.combine(today, time(10, 5), tzinfo=KST), client_factory=lambda cred: fake2,
                               sleep_fn=lambda _s: None, plan_fn=_plan(LINES[:3], today, 97500.0))
    assert ei3.value.status_code == 409 and "정지" in ei3.value.detail


def test_late_run_limit_blocks_catch_up_execution():
    """지연 상한 (2026-09-09 사고: 낡은 beat 상태 파일로 배포마다 지난 크론 재실행) — 09:30 을 넘겨 도착한 09:01/09:15 실행은
    발주하지 않고 'late' 로 기록·오류 로그. 오늘 이미 돈 포트는 already-ran. 상한을 끄면(None) 종전처럼 돈다."""
    import app.autoexec as ae

    assert ae.LATE_RUN_LIMIT == time(9, 30)
    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True}, headers=h)
    fake = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec, _ = _run(fake, aid, today, LINES[:2], at=(11, 0))
    assert rec["note"] == "late" and "지연 상한 09:30 초과" in rec["reason"] and fake.placed == []
    st, view = _orders(c, h, pid, today)
    assert st == {} and view["state"]["code"] == "ran" and view["state"]["label"] == "발주 안 함 — 09:30 지연 상한 초과" and view["retryable"] == 0
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.run"]
    assert ev and ev[0]["level"] == "error" and "지연 상한" in ev[0]["text"]
    rec2, _ = _run(fake, aid, today, LINES[:2], at=(11, 5), trigger="watchdog")
    assert rec2["error"] == "already-ran" and fake.placed == []
    # 상한 없음(테스트·수동 재실행) — 다른 포트에서 11:00 에도 발주
    c2, h2 = _client()
    pid2, aid2 = _setup_portfolio(c2, h2, today, deposit_krw=9_000_000)
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"buy": True}, headers=h2)
    fake2 = FakeKis(open_px=100000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, time(11, 0), tzinfo=KST), client_factory=lambda cred: fake2,
                                    sleep_fn=lambda _s: None, plan_fn=_plan(LINES[:2], today), only_credential_ids={aid2}, late_limit=None)
    assert out["portfolios"][0]["submitted"] == 2 and len(fake2.placed) == 2


class FillKis(FakeKis):
    """체결조회 결과를 지정할 수 있는 대역 — {주문번호: 체결수량}."""

    def __init__(self, *a, fills: dict | None = None, **kw):
        super().__init__(*a, **kw)
        self.fills = dict(fills or {})

    def fetch_executions(self, start, end, only_filled=True):
        class E:
            def __init__(self, no, q):
                self.order_no, self.filled_qty = no, q
        return [E(no, q) for no, q in self.fills.items()]


def test_cancel_refuses_filled_order_and_reorder_replaces_cancelled_line(monkeypatch):
    """체결된 주문은 취소되지 않는다 (2026-09-10 지시) — 상태가 '발주됨' 으로 낡았어도 취소 직전 체결조회로 맞추고 전량 체결이면 409.
    취소·실패·생략된 줄은 '재등록'(REORDERABLE)으로 그 줄만 다시 발주한다. 갭 생략·체결된 줄은 재등록 대상 외."""
    from unittest.mock import patch

    import app.autoexec as ae
    import app.broker as br
    from app.models import TradePortfolio

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True}, headers=h)

    def cancel(oid, at=(10, 0)):
        """장중 시각 고정 — 취소 직전 체결 재확인이 이 시계를 쓴다. 15:30 뒤에 돌리면 미체결이 unfilled 로 확정돼
        취소가 409 가 된다(장 마감 후에는 취소할 것이 없다) — 테스트가 시각에 흔들리지 않게 (2026-09-10 실측)."""
        with patch("app.broker.datetime") as dt:
            dt.now.return_value = datetime.combine(today, time(*at), tzinfo=KST)
            return c.post(f"/portfolio/{pid}/orders/{oid}/cancel", headers=h)
    fake = FillKis(open_px=100_000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    rec, _ = _run(fake, aid, today, LINES[:2])
    assert rec["submitted"] == 2 and fake.placed == [("069500", "buy", 5, 99000), ("069500", "buy", 3, 98000)]
    st, _ = _orders(c, h, pid, today)
    g1, g2 = st["grid1"], st["grid2"]
    assert g1["status"] == "submitted" and g1["order_no"] == "N0001" and g2["order_no"] == "N0002"

    monkeypatch.setattr(br, "_client", lambda cred: fake)
    monkeypatch.setattr(ae, "_client", lambda cred: fake)
    # ① grid1 은 이미 전량 체결 — 화면 상태는 '발주됨' 이지만 취소 요청은 거부되고, 상태가 체결로 바로잡힌다
    fake.fills = {"N0001": 5}
    r = cancel(g1["id"])
    assert r.status_code == 409 and "이미 전량 체결" in r.json()["detail"] and "5주 체결" in r.json()["detail"]
    assert fake.cancelled == []                                   # 증권사에 취소 요청을 보내지 않았다
    st, _ = _orders(c, h, pid, today)
    assert st["grid1"]["status"] == "filled" and st["grid1"]["filled_qty"] == 5
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "order.cancel"]
    assert ev and "취소 불가(이미 체결)" in ev[0]["text"]
    # ② grid2 는 미체결 — 취소되고 잔량이 없으므로 메시지에 체결 언급 없음
    r2 = cancel(g2["id"])
    assert r2.status_code == 200 and fake.cancelled == ["N0002"]
    st, _ = _orders(c, h, pid, today)
    assert st["grid2"]["status"] == "cancelled"
    # ③ 체결된 줄은 재등록 대상이 아니다
    assert c.post(f"/portfolio/{pid}/orders/{st['grid1']['id']}/reorder", headers=h).status_code == 409
    # ④ 취소된 줄만 재등록 — 같은 절차로 다시 발주(새 행), grid1 은 건드리지 않는다
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid)
        res = ae.retry_auto_exec(s, pf, now=datetime.combine(today, time(10, 30), tzinfo=KST), client_factory=lambda cred: fake,
                                 sleep_fn=lambda _s: None, plan_fn=_plan(LINES[:2], today),
                                 statuses=ae.REORDERABLE, only_lines={st["grid2"]["line_key"]}, what="재등록")
        s.commit()
    assert res["n"] == 1 and res["submitted"] == 1 and fake.placed[-1] == ("069500", "buy", 3, 98000)
    st2, view = _orders(c, h, pid, today)
    assert st2["grid2"]["status"] == "submitted" and st2["grid2"]["order_no"] == "N0003"
    assert st2["grid1"]["status"] == "filled" and st2["grid1"]["order_no"] == "N0001"
    items = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]
    g2_rows = [i for i in items if i["kind"] == "grid2"]
    assert [i["status"] for i in g2_rows] == ["cancelled", "submitted"] and g2_rows[1]["retry_of"] == g2_rows[0]["id"]
    ev2 = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.retry"]
    assert ev2 and "무인 재등록" in ev2[0]["text"]


def test_manual_order_places_cancels_and_shows_with_auto(monkeypatch):
    """앱에서 직접 KIS 주문 (2026-09-10 지시, ADR-011) — 장중만, 매수는 주문가능 수량·매도는 잔고로 사전 검증,
    mode='manual' 로 기록돼 취소·체결 확정·주문표 표시가 무인과 같은 경로를 탄다."""
    import app.broker as br
    from unittest.mock import patch

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)
    fake = FillKis(open_px=100_000, deposit=9_000_000, holdings={"069500": 4}, psbl_cash=600_000)
    monkeypatch.setattr(br, "_client", lambda cred: fake)

    def order(body, at=(10, 0)):
        with patch("app.broker.datetime") as dt:      # 장중 시각 고정 — 테스트가 시각에 흔들리지 않게
            dt.now.return_value = datetime.combine(today, time(*at), tzinfo=KST)
            return c.post(f"/portfolio/{pid}/orders/manual", json=body, headers=h)

    def cancel(oid, at=(10, 0)):                      # 취소 직전 체결 재확인도 같은 시계 (15:30 뒤면 unfilled 로 확정돼 409)
        with patch("app.broker.datetime") as dt:
            dt.now.return_value = datetime.combine(today, time(*at), tzinfo=KST)
            return c.post(f"/portfolio/{pid}/orders/{oid}/cancel", headers=h)

    # ① 장 마감 뒤에는 거부
    r = order({"code": "069500", "side": "buy", "qty": 1, "price": 99_000}, at=(16, 0))
    assert r.status_code == 409 and "장중" in r.json()["detail"]
    # ② 매수가능 수량 초과 → 거부하고 주문을 내지 않는다 (600,000 ÷ 99,000 = 6주)
    r = order({"code": "069500", "side": "buy", "qty": 20, "price": 99_000})
    assert r.status_code == 409 and "주문가능 수량 부족" in r.json()["detail"] and fake.placed == []
    # ③ 잔고 초과 매도 → 거부
    r = order({"code": "069500", "side": "sell", "qty": 10, "price": 103_000})
    assert r.status_code == 409 and "잔고 부족" in r.json()["detail"] and fake.placed == []
    # ④ 정상 접수 — mode=manual, 주문번호 기록
    r = order({"code": "069500", "side": "buy", "qty": 5, "price": 99_000})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["mode"] == "manual" and row["status"] == "submitted" and row["order_no"] == "N0001"
    assert fake.placed == [("069500", "buy", 5, 99000)]
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "order.manual"]
    assert ev and "직접 주문" in ev[0]["text"] and "5주" in ev[0]["text"]
    # ⑤ 목록에 무인과 함께 보이고 취소도 같은 경로 — 미체결이면 취소, 체결이면 거부
    items = c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]
    mine = [i for i in items if i["mode"] == "manual"]
    assert len(mine) == 1 and mine[0]["kind"] == "manual"
    monkeypatch.setattr(ae_mod(), "_client", lambda cred: fake)
    fake.fills = {"N0001": 5}
    assert cancel(mine[0]["id"]).status_code == 409   # 전량 체결 → 취소 불가
    fake.fills = {}
    r2 = order({"code": "069500", "side": "sell", "qty": 2, "price": 103_000})
    assert r2.status_code == 200 and fake.placed[-1] == ("069500", "sell", 2, 103000)
    assert cancel(r2.json()["id"]).status_code == 200
    assert fake.cancelled == ["N0002"]


def ae_mod():
    import app.autoexec as ae
    return ae
