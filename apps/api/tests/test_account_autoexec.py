"""무인 매매 플래그를 증권사 계좌별로 (2026-09-07 지시, ADR-009) — 설정 API(기본값·일괄·계좌별·상한·상속·격리), 실행은 포트에 연결된 계좌의 플래그로만 판정."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]
KST = timezone(timedelta(hours=9))
DEFAULT = {"buy": False, "sell": False, "daily_buy_cap_pct": 20.0}


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"ac{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _acct(c, h, label, env="prod", no="68800037-01"):
    return c.post("/broker/accounts", json={"label": label, "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": no, "env": env}, headers=h).json()


def test_settings_default_bulk_per_account_cap_and_inheritance():
    c, h = _client()
    g = c.get("/settings/auto-exec", headers=h).json()
    assert g["default"] == DEFAULT and g["accounts"] == [] and g["buy"] is False
    a1, a2 = _acct(c, h, "위탁1"), _acct(c, h, "위탁2", no="68800038-01")
    g = c.get("/settings/auto-exec", headers=h).json()
    assert [a["label"] for a in g["accounts"]] == ["위탁1", "위탁2"] and all(a["auto_exec"] == DEFAULT for a in g["accounts"])
    # 일괄 = 기본값 + 모든 계좌
    g = c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h).json()
    assert g["default"]["buy"] is True and all(a["auto_exec"]["buy"] is True and a["auto_exec"]["sell"] is False for a in g["accounts"])
    # 계좌별 — 생략 키 유지, 상한도 계좌별
    g = c.put(f"/settings/auto-exec/accounts/{a2['id']}", json={"buy": False, "sell": True}, headers=h).json()
    by = {a["label"]: a["auto_exec"] for a in g["accounts"]}
    assert by["위탁1"] == {"buy": True, "sell": False, "daily_buy_cap_pct": 20.0}
    assert by["위탁2"] == {"buy": False, "sell": True, "daily_buy_cap_pct": 20.0}
    assert g["default"]["buy"] is True   # 기본값은 일괄 때만 바뀐다
    g = c.put(f"/settings/auto-exec/accounts/{a2['id']}", json={"daily_buy_cap_pct": 10}, headers=h).json()
    assert {a["label"]: a["auto_exec"] for a in g["accounts"]}["위탁2"] == {"buy": False, "sell": True, "daily_buy_cap_pct": 10.0}
    assert c.put(f"/settings/auto-exec/accounts/{a2['id']}", json={"daily_buy_cap_pct": 120}, headers=h).status_code == 422
    # 새 계좌는 사용자 기본값 상속
    a3 = _acct(c, h, "위탁3", no="68800039-01")
    assert a3["auto_exec"] == {"buy": True, "sell": False, "daily_buy_cap_pct": 20.0}
    # 다른 사용자의 계좌는 404, 목록에도 없음
    c2, h2 = _client()
    assert c.put(f"/settings/auto-exec/accounts/{a1['id']}", json={"buy": True}, headers=h2).status_code == 404
    assert c.get("/settings/auto-exec", headers=h2).json()["accounts"] == []
    ev = [i for i in c.get("/logs?type=event", headers=h).json()["items"] if i["kind"] == "autoexec.account_setting"]
    assert any("계좌 위탁2" in e["text"] and "상한 10%" in e["text"] for e in ev)


def test_execution_uses_linked_account_flags_only():
    """판정은 포트에 연결된 계좌의 플래그 — 같은 사용자의 다른 계좌를 켜도 수동 모드. 매수만 켠 계좌는 매수 줄만 발주, 매도 줄은 '수동 처리'."""
    from tests.test_autoexec import LINES, FakeKis, _orders, _run, _setup_portfolio

    c, h = _client()
    today = datetime.now(KST).date()
    pid, aid = _setup_portfolio(c, h, today, deposit_krw=9_000_000)   # 계좌 '위탁' 연결, 플래그 기본 꺼짐
    other = _acct(c, h, "다른계좌", no="68800040-01")
    c.put(f"/settings/auto-exec/accounts/{other['id']}", json={"buy": True, "sell": True}, headers=h)
    view = c.get(f"/portfolio/{pid}/auto-exec?date={today.isoformat()}", headers=h).json()
    assert view["allowed"] == {"buy": False, "sell": False, "daily_buy_cap_pct": 20.0} and view["account"]["label"] == "위탁"
    assert view["state"]["code"] == "off" and "위탁" in view["state"]["label"]
    # 연결 계좌의 매수만 켬 → 상태 '무인 매수 대기', 09:01 에 매수 줄만 발주
    c.put(f"/settings/auto-exec/accounts/{aid}", json={"buy": True}, headers=h)
    tomorrow = (today + timedelta(days=1)).isoformat()
    view = c.get(f"/portfolio/{pid}/auto-exec?date={tomorrow}", headers=h).json()
    assert view["allowed"]["buy"] is True and view["state"]["code"] == "waiting" and "무인 매수 대기" in view["state"]["label"]
    fake = FakeKis(open_px=100_000, deposit=9_000_000, holdings={"069500": 0}, psbl_cash=9_000_000)
    rec, _ = _run(fake, aid, today, LINES[:3])
    assert rec["submitted"] == 2 and rec["skipped"] == 1 and fake.placed == [("069500", "buy", 5, 99000), ("069500", "buy", 3, 98000)]
    st, _ = _orders(c, h, pid, today)
    assert st["tp"]["status"] == "skipped" and st["tp"]["message"] == "무인 매도 꺼짐 — 수동 처리"
    assert st["grid1"]["status"] == "submitted" and st["grid2"]["status"] == "submitted"
