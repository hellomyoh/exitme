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


def test_account_store_and_portfolio_link():
    """설정에서 계좌 등록 → 실전매매 포트에 연결 (0017): 마스킹·재사용·격리·해제."""
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "연동테스트"}, headers=h).json()["id"]
    pid2 = c.post("/portfolios", json={"name": "두번째"}, headers=h).json()["id"]
    assert c.get(f"/portfolio/{pid}/broker", headers=h).json() == {"linked": False}
    assert c.get("/broker/accounts", headers=h).json()["items"] == []

    body = {"label": "메인 계좌", "app_key": "PSxxxxxxxxxxxxxxxxxx", "app_secret": "SEC" + "y" * 40,
            "account_no": "12345678-22", "env": "vps"}
    acct = c.post("/broker/accounts", json=body, headers=h).json()
    assert acct["label"] == "메인 계좌" and acct["acnt_prdt_cd"] == "22" and acct["env"] == "vps"
    assert "****" in acct["app_key"] and body["app_key"] not in acct["app_key"]
    lst = c.get("/broker/accounts", headers=h)
    assert body["app_secret"] not in lst.text and len(lst.json()["items"]) == 1

    # 같은 계좌를 두 포트에 연결(재사용) — 키 재입력 없음
    for p in (pid, pid2):
        r = c.put(f"/portfolio/{p}/broker", json={"credential_id": acct["id"]}, headers=h)
        assert r.status_code == 200 and r.json()["linked"] is True
    linked = c.get("/broker/accounts", headers=h).json()["items"][0]["linked_portfolios"]
    assert set(linked) == {"연동테스트", "두번째"}

    # 연결 해제
    assert c.put(f"/portfolio/{pid2}/broker", json={"credential_id": None}, headers=h).json()["linked"] is False
    assert c.post(f"/portfolio/{pid2}/import-fills", headers=h).status_code == 409  # 미연결
    # 타인 계좌 연결 시도 차단
    _, h2 = _client()
    assert c.get("/broker/accounts", headers=h2).json()["items"] == []
    assert c.delete(f"/broker/accounts/{acct['id']}", headers=h2).status_code == 404
    pid3 = c.post("/portfolios", json={"name": "타인포트"}, headers=h2).json()["id"]
    assert c.put(f"/portfolio/{pid3}/broker", json={"credential_id": acct["id"]},
                 headers=h2).status_code == 404
    # 계좌 삭제 → 연결 자동 해제
    assert c.delete(f"/broker/accounts/{acct['id']}", headers=h).json()["deleted"] is True
    assert c.get(f"/portfolio/{pid}/broker", headers=h).json() == {"linked": False}


def test_import_fills_dry_run_and_idempotent(monkeypatch):
    """미리보기는 등록하지 않고, 실제 등록은 broker_ref 로 중복을 막는다."""
    from app import broker as bk
    from app.services.kis_client import Execution

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "가져오기"}, headers=h).json()["id"]
    aid = c.post("/broker/accounts", json={"app_key": "PS" + "k" * 18, "app_secret": "S" * 42,
                                           "account_no": "12345678"}, headers=h).json()["id"]
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": aid}, headers=h)
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
    base = {"app_key": "PS" + "k" * 18, "app_secret": "S" * 42}
    r = c.post("/broker/accounts", json={**base, "account_no": "87654321-22"}, headers=h)
    assert r.status_code == 201 and r.json()["acnt_prdt_cd"] == "22"
    # 자릿수 부족 → 422
    assert c.post("/broker/accounts", json={**base, "account_no": "123456"},
                  headers=h).status_code == 422


def test_probe_account_finds_product_code(monkeypatch):
    """계좌 확인 (2026-09-05): 상품코드 미지정 시 후보를 훑어 유효한 계좌만 돌려준다."""
    from app.services import kis_client as kc

    c, h = _client()
    calls = []

    def fake_probe(self, prdt=None):
        cd = prdt or self.acnt_prdt_cd
        calls.append(cd)
        if cd != "22":
            raise kc.KisError(f"KIS error rt_cd=1 msg=계좌없음({cd})")
        return {"ok": True, "holdings": 2, "deposit": 1000000, "total_eval": 5000000}

    monkeypatch.setattr(kc.KisTradingClient, "probe_balance", fake_probe)
    monkeypatch.setattr(kc.KisAuth, "access_token", lambda self, session=None: "tok")

    body = {"app_key": "PS" + "k" * 18, "app_secret": "S" * 42, "account_no": "12345678-"}
    r = c.post("/broker/probe", json=body, headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert [a["label"] for a in j["accounts"]] == ["12345678-22"]
    assert j["accounts"][0]["deposit"] == 1000000 and j["accounts"][0]["holdings"] == 2
    assert calls[0] == "01"  # 후보 순서대로 시도

    # 전부 실패 → 502
    monkeypatch.setattr(kc.KisTradingClient, "probe_balance",
                        lambda self, prdt=None: (_ for _ in ()).throw(kc.KisError("계좌없음")))
    assert c.post("/broker/probe", json=body, headers=h).status_code == 502
    # 미인증 차단
    assert TestClient(app, base_url="https://testserver").post("/broker/probe", json=body).status_code == 401


def test_account_update_keeps_keys_when_blank():
    """계좌 수정 (2026-09-05): 라벨·계좌·환경 변경, 키는 비우면 유지·입력 시 교체, 타인 404."""
    c, h = _client()
    created = c.post("/broker/accounts", json={
        "label": "옛이름", "app_key": "PS" + "a" * 18, "app_secret": "S" * 42,
        "account_no": "12345678-01", "env": "prod"}, headers=h).json()
    aid, old_key_mask = created["id"], created["app_key"]

    # 키 없이 라벨·계좌·환경만 수정 → 키 유지
    r = c.put(f"/broker/accounts/{aid}", json={"label": "새이름", "account_no": "87654321-22",
                                               "env": "vps"}, headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["label"] == "새이름" and j["acnt_prdt_cd"] == "22" and j["env"] == "vps"
    assert j["app_key"] == old_key_mask  # 마스킹 값 동일 = 키 그대로

    # 빈 문자열도 유지로 간주
    assert c.put(f"/broker/accounts/{aid}", json={"app_key": "", "app_secret": ""},
                 headers=h).json()["app_key"] == old_key_mask
    # 키 교체
    j2 = c.put(f"/broker/accounts/{aid}", json={"app_key": "PS" + "z" * 18}, headers=h).json()
    assert j2["app_key"] != old_key_mask and j2["app_key"].startswith("PSzz")
    # 잘못된 계좌번호 → 422, 타인 → 404
    assert c.put(f"/broker/accounts/{aid}", json={"account_no": "123"}, headers=h).status_code == 422
    _, h2 = _client()
    assert c.put(f"/broker/accounts/{aid}", json={"label": "탈취"}, headers=h2).status_code == 404
