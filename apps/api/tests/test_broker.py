"""증권사 조회 연동 — 자격 저장·마스킹·격리, 체결 가져오기 멱등, 계획 대조 (2026-09-05)."""
import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.broker import reconcile_plan
from app.main import app


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"bk{uuid.uuid4().hex[:8]}@x.dev",
                                         "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_reconcile_plan_cases():
    """계획 vs 체결 4가지: 일치(경고 없음)·미이행·계획 외·수량 불일치."""
    plan = [{"instrument": "K200", "side": "buy", "qty": 26, "price": 103840},
            {"instrument": "K200", "side": "sell", "qty": 461, "price": 112500}]
    # 완전 일치 → 경고 없음
    assert reconcile_plan(plan, [{"leg": "K200", "side": "buy", "qty": 26, "price": 103840},
                                 {"leg": "K200", "side": "sell", "qty": 461, "price": 112500}]) == []
    # 미이행(매수 등록 없음)
    out = reconcile_plan(plan, [{"leg": "K200", "side": "sell", "qty": 461, "price": 112500}])
    assert len(out) == 1 and out[0]["level"] == "info" and "체결 없음" in out[0]["text"]
    # 수량 불일치
    out = reconcile_plan(plan, [{"leg": "K200", "side": "buy", "qty": 20, "price": 103840},
                                {"leg": "K200", "side": "sell", "qty": 461, "price": 112500}])
    assert len(out) == 1 and out[0]["level"] == "warn" and "-6주" in out[0]["text"]
    # 계획에 없던 거래
    out = reconcile_plan([], [{"leg": "LEV", "side": "buy", "qty": 3, "price": 9000}])
    assert len(out) == 1 and out[0]["level"] == "warn" and "계획에 없던" in out[0]["text"]


def test_broker_credentials_crud_and_isolation():
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "연동테스트"}, headers=h).json()["id"]
    assert c.get(f"/portfolio/{pid}/broker", headers=h).json() == {"linked": False}
    body = {"app_key": "PSxxxxxxxxxxxxxxxxxx", "app_secret": "SEC" + "y" * 40,
            "account_no": "12345678", "acnt_prdt_cd": "01", "env": "vps"}
    assert c.put(f"/portfolio/{pid}/broker", json=body, headers=h).json()["linked"] is True
    got = c.get(f"/portfolio/{pid}/broker", headers=h).json()
    assert got["linked"] and got["env"] == "vps"
    assert "****" in got["app_key"] and body["app_key"] not in got["app_key"]   # 마스킹
    assert "****" in got["account_no"]
    # 평문이 응답에 없어야 함
    assert body["app_secret"] not in c.get(f"/portfolio/{pid}/broker", headers=h).text
    # 타인 접근 차단
    _, h2 = _client()
    assert c.get(f"/portfolio/{pid}/broker", headers=h2).status_code == 404
    assert c.post(f"/portfolio/{pid}/import-fills", headers=h2).status_code == 404
    # 연동 없는 포트에서 가져오기 → 409
    pid2 = c.post("/portfolios", json={"name": "미연동"}, headers=h).json()["id"]
    assert c.post(f"/portfolio/{pid2}/import-fills", headers=h).status_code == 409
    assert c.delete(f"/portfolio/{pid}/broker", headers=h).json()["deleted"] is True


def test_import_fills_dry_run_and_idempotent(monkeypatch):
    """미리보기는 등록하지 않고, 실제 등록은 broker_ref 로 중복을 막는다."""
    from app import broker as bk
    from app.services.kis_client import Execution

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "가져오기"}, headers=h).json()["id"]
    c.put(f"/portfolio/{pid}/broker", json={"app_key": "PS" + "k" * 18, "app_secret": "S" * 42,
                                            "account_no": "12345678"}, headers=h)
    d = date.today() - timedelta(days=1)
    fake = [Execution(order_no="A1", trade_date=d, code="069500", side="buy",
                      filled_qty=10, avg_price=30000, order_qty=10, remain_qty=0, name="KODEX 200"),
            Execution(order_no="A2", trade_date=d, code="999999", side="buy",
                      filled_qty=1, avg_price=100, order_qty=1, remain_qty=0, name="미시딩")]

    class FakeClient:
        def fetch_executions(self, start, end, only_filled=True):
            return fake

    monkeypatch.setattr(bk, "_client", lambda cred: FakeClient())
    prev = len(c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()["positions"])

    r = c.post(f"/portfolio/{pid}/import-fills?days=3&dry_run=true", headers=h).json()
    assert r["dry_run"] and r["added"] == 0 and r["fetched"] == 2
    assert r["unknown_codes"] == ["999999"]
    assert len(c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()["positions"]) == prev

    r = c.post(f"/portfolio/{pid}/import-fills?days=3&dry_run=false", headers=h).json()
    assert r["added"] == 1 and r["skipped"] == 0
    r2 = c.post(f"/portfolio/{pid}/import-fills?days=3&dry_run=false", headers=h).json()
    assert r2["added"] == 0 and r2["skipped"] == 1  # 멱등
    pos = c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()["positions"]
    assert any(p["code"] == "069500" and p["qty"] == 10 for p in pos)


def test_account_number_normalization():
    """계좌번호 입력 정규화 (2026-09-05): 10자리·하이픈·공백 입력을 CANO 8 + 상품코드 2 로 분리."""
    from app.broker import split_account

    assert split_account("12345678-01") == ("12345678", "01")
    assert split_account("12345678 22") == ("12345678", "22")
    assert split_account("1234567801") == ("12345678", "01")
    assert split_account("12345678", "22") == ("12345678", "22")  # 8자리면 입력 상품코드 유지

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "계좌형식"}, headers=h).json()["id"]
    base = {"app_key": "PS" + "k" * 18, "app_secret": "S" * 42}
    r = c.put(f"/portfolio/{pid}/broker", json={**base, "account_no": "87654321-22"}, headers=h)
    assert r.status_code == 200 and r.json()["acnt_prdt_cd"] == "22"
    assert c.get(f"/portfolio/{pid}/broker", headers=h).json()["acnt_prdt_cd"] == "22"
    # 자릿수 부족 → 422
    assert c.put(f"/portfolio/{pid}/broker", json={**base, "account_no": "123456"},
                 headers=h).status_code == 422
