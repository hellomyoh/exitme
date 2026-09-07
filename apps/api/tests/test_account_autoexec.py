"""무인 실행 허용을 증권사 계좌별로 (2026-09-07 지시) — 설정 API(기본값·일괄·계좌별·상속·격리), 승인 전·발주 직전 계좌 스위치 검사,
자동 승인·사전 갭 취소의 계좌 스위치 사용, 매수만 허용 시 매수만 자동 발주."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
    tok = c.post("/auth/register", json={"email": f"ac{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _acct(c, h, label, env="prod", no="68800037-01"):
    return c.post("/broker/accounts", json={"label": label, "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": no, "env": env}, headers=h).json()


def test_settings_default_bulk_per_account_and_inheritance():
    """GET = 기본값 + 계좌 목록. PUT(전체) = 기본값 + 모든 계좌 일괄. PUT(계좌) = 그 계좌만. 새 계좌는 기본값 상속. 타인 계좌 404."""
    c, h = _client()
    g = c.get("/settings/auto-exec", headers=h).json()
    assert g["default"] == {"buy": False, "sell": False, "preopen_cancel": True} and g["accounts"] == [] and g["buy"] is False
    a1, a2 = _acct(c, h, "위탁1"), _acct(c, h, "위탁2", no="68800038-01")
    g = c.get("/settings/auto-exec", headers=h).json()
    assert [a["label"] for a in g["accounts"]] == ["위탁1", "위탁2"] and all(a["auto_exec"] == g["default"] for a in g["accounts"])
    # 일괄
    g = c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h).json()
    assert g["default"]["buy"] is True and all(a["auto_exec"]["buy"] is True and a["auto_exec"]["sell"] is False for a in g["accounts"])
    # 계좌별 — 위탁2 만 매수 끔·매도 켬, 위탁1 은 그대로
    g = c.put(f"/settings/auto-exec/accounts/{a2['id']}", json={"buy": False, "sell": True}, headers=h).json()
    by = {a["label"]: a["auto_exec"] for a in g["accounts"]}
    assert by["위탁1"] == {"buy": True, "sell": False, "preopen_cancel": True}
    assert by["위탁2"] == {"buy": False, "sell": True, "preopen_cancel": True}
    assert g["default"]["buy"] is True   # 기본값은 일괄 때만 바뀐다
    # 생략한 키는 유지
    g = c.put(f"/settings/auto-exec/accounts/{a2['id']}", json={"preopen_cancel": False}, headers=h).json()
    assert {a["label"]: a["auto_exec"] for a in g["accounts"]}["위탁2"] == {"buy": False, "sell": True, "preopen_cancel": False}
    # 새 계좌는 사용자 기본값을 상속
    a3 = _acct(c, h, "위탁3", no="68800039-01")
    assert a3["auto_exec"] == {"buy": True, "sell": False, "preopen_cancel": True}
    # 격리
    _, h2 = _client()
    assert c.put(f"/settings/auto-exec/accounts/{a1['id']}", json={"buy": True}, headers=h2).status_code == 404
    assert c.get("/settings/auto-exec", headers=h2).json()["accounts"] == []
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.account_setting"]
    assert ev and "계좌 위탁2" in ev[0]["text"]


def test_approval_and_execution_check_linked_account_switch(monkeypatch):
    """승인 전: 포트에 연결된 계좌의 스위치로 판정(같은 사용자라도 다른 계좌면 거절). 발주 직전: 승인 뒤 계좌 스위치를 끄면 생략."""
    import app.autoexec as ae
    from tests.test_autoexec import LINES, FakeKis, _setup_portfolio

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, LINES, gap_exact=None)          # 계좌 '위탁' 연결, 스위치 기본 꺼짐
    other = _acct(c, h, "다른계좌", no="68800040-01")
    # 다른 계좌만 켜도 이 포트는 거절 — 판정은 연결 계좌 기준
    c.put(f"/settings/auto-exec/accounts/{other['id']}", json={"buy": True}, headers=h)
    monkeypatch.setattr(ae, "OPEN_TIME", ae.time(23, 59))
    r = c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[0]]}, headers=h)
    assert r.status_code == 409 and "계좌 위탁" in r.json()["detail"] and "무인 매수" in r.json()["detail"]
    view = c.get(f"/portfolio/{pid}/auto-exec", headers=h).json()
    assert view["allowed"]["buy"] is False and view["account"]["label"] == "위탁"
    # 연결 계좌를 켜면 승인된다
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True}, headers=h)
    assert c.get(f"/portfolio/{pid}/auto-exec", headers=h).json()["allowed"]["buy"] is True
    r = c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[0], LINES[1]]}, headers=h).json()
    assert r["approved"] == 2
    # 매도 줄은 계좌의 매도 허용이 꺼져 있어 거절
    assert c.post(f"/portfolio/{pid}/orders/approve", json={"date": today.isoformat(), "lines": [LINES[2]]}, headers=h).status_code == 409
    # 승인 뒤 계좌 스위치를 끄면 09:01 발주 직전 재검사에서 생략
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": False}, headers=h)
    fake = FakeKis(open_px=100_000, deposit=9_000_000, holdings={}, psbl_cash=9_000_000)
    idle = FakeKis(open_px=100_000, deposit=0, holdings={})
    with SessionLocal() as s:
        out = ae.run_auto_execution(s, now=datetime.combine(today, ae.time(9, 1), tzinfo=KST),
                                    client_factory=lambda cred: fake if cred.id == aid else idle, sleep_fn=lambda _s: None)
    rec = next(r for r in out["portfolios"] if r["portfolio_id"] == pid)
    assert rec["submitted"] == 0 and rec["skipped"] == 2 and fake.placed == []
    st = {i["kind"]: i for i in c.get(f"/portfolio/{pid}/orders?date={today.isoformat()}", headers=h).json()["items"]}
    assert all("계좌 설정에서 무인 매수 허용이 꺼져 있어 생략" in st[k]["message"] for k in ("grid1", "grid2"))


def test_auto_approve_and_preopen_use_account_switch(monkeypatch):
    """매수만 허용한 계좌: 자동 승인이 매수 줄만 승인(매도는 '수동 필요'), 익절 매도 미승인. 사전 갭 취소는 계좌 스위치로 건너뜀."""
    import app.autoapprove as aa
    from tests.test_autoapprove import LINES as ALINES, FakeBroker, _next_weekday, _rec, _run, _setup
    from tests.test_preopen import FakePre, _open
    from tests.test_preopen import _run as _prun
    from tests.test_preopen import _setup as _psetup

    c, h = _client()
    exec_day = _next_weekday(datetime.now(KST).date())
    pid, aid = _setup(c, h, exec_day)                                          # 자동 승인 기본 켬
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True, "sell": False}, headers=h)
    monkeypatch.setattr(aa, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "ok"})
    rec = _rec(_run(exec_day, FakeBroker(), aid, lines=ALINES), pid)
    assert rec["approved"] == 2 and rec["reserved"] == 1 and rec["manual"] == ["tp (매도 허용 꺼짐)"]
    st = {i["kind"]: i for i in c.get(f"/portfolio/{pid}/orders?date={exec_day.isoformat()}", headers=h).json()["items"]}
    assert st["grid1"]["status"] == "approved" and st["grid2"]["status"] == "approved" and "tp" not in st
    # 계좌 스위치를 모두 끄면 자동 승인 건너뜀(사유에 계좌 라벨)
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": False}, headers=h)
    rec2 = _rec(_run(exec_day, FakeBroker(), aid, lines=ALINES), pid)
    assert rec2["note"] == "계좌 설정에서 무인 매수·매도 모두 꺼짐"
    # 사전 갭 취소 — 계좌의 스위치가 꺼지면 조회조차 하지 않는다
    c2, h2 = _client()
    today = datetime.now(KST).date()
    pid2, aid2 = _psetup(c2, h2, today)
    c2.put(f"/settings/auto-exec/accounts/{aid2}", json={"preopen_cancel": False}, headers=h2)
    fake2 = FakePre(expected=90_000, open_orders=[_open("Z1", 99_000)])
    out = _prun(fake2, today, aid2)
    r2 = next(r for r in out["portfolios"] if r["portfolio_id"] == pid2)
    assert r2["note"] == "설정 꺼짐" and fake2.expected_calls == 0
