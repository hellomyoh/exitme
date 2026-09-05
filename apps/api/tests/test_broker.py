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


def test_masked_values_exposed_for_edit():
    """수정 화면용 마스킹 (2026-09-05): 앱키·시크릿·계좌를 중간 가림으로 내보내되 평문은 절대 노출 안 함."""
    from app.broker import _mask

    assert _mask("12345678") == "1234**78"          # 8자리 계좌
    assert _mask("PS1234567890ABCD") == "PS12********CD"
    assert _mask("ab", 2, 2) == "**"                 # 너무 짧으면 전부 가림
    key, secret = "PS" + "k" * 30, "SEC" + "z" * 170
    c, h = _client()
    made = c.post("/broker/accounts", json={"label": "마스킹", "app_key": key,
                                            "app_secret": secret, "account_no": "10041234-22"},
                  headers=h).json()
    assert made["app_key"].startswith("PS") and made["app_key"].endswith(key[-2:])
    assert "*" in made["app_key"] and key not in made["app_key"]
    assert "*" in made["app_secret"] and secret not in made["app_secret"]
    assert made["account_no"] == "1004**34" and made["acnt_prdt_cd"] == "22"
    body = c.get("/broker/accounts", headers=h).text
    assert key not in body and secret not in body    # 평문 미노출
    # 삭제된 계좌 수정 → 404 (화면은 이 응답으로 목록을 새로 고친다)
    c.delete(f"/broker/accounts/{made['id']}", headers=h)
    assert c.put(f"/broker/accounts/{made['id']}", json={"label": "x"}, headers=h).status_code == 404


def test_kis_error_messages_are_actionable():
    """오류 안내 (2026-09-05): KIS 원문 코드를 조치 가능한 한국어로 바꿔 전달한다."""
    from app.broker import humanize_kis_error

    assert "계좌번호가 올바르지 않습니다" in humanize_kis_error("KIS error rt_cd=2 msg=ERROR : INPUT INVALID_CHECK_ACNO")
    assert "앱키가 유효하지 않습니다" in humanize_kis_error("유효하지 않은 AppKey입니다. (HTTP 403, EGW00103)")
    assert "분당 1회" in humanize_kis_error("EGW00133 접근 과다")
    assert humanize_kis_error("알 수 없는 오류") == "알 수 없는 오류"  # 매핑 없으면 원문 유지


def test_truncated_secret_is_rejected():
    """잘린 자격 차단 (2026-09-05): 9자짜리 시크릿이 저장돼 조회에서만 EGW00304 로 실패하던 사고 재발 방지."""
    c, h = _client()
    r = c.post("/broker/accounts", json={"app_key": "PS" + "k" * 18, "app_secret": "SShortSec",
                                         "account_no": "12345678", "acnt_prdt_cd": "01"}, headers=h)
    assert r.status_code == 422 and "앱시크릿" in r.json()["detail"]

    aid = c.post("/broker/accounts", json={"app_key": "PS" + "k" * 18, "app_secret": "S" * 42,
                                           "account_no": "12345678", "acnt_prdt_cd": "01"},
                 headers=h).json()["id"]
    # 수정 경로도 같은 기준 — 여기에 구멍이 있어 짧은 값이 들어갔다
    bad = c.put(f"/broker/accounts/{aid}", json={"app_secret": "SShortSec"}, headers=h)
    assert bad.status_code == 422 and "앱시크릿" in bad.json()["detail"]
    # 비워 두면 기존 값 유지 (검사 대상 아님)
    assert c.put(f"/broker/accounts/{aid}", json={"label": "그대로"}, headers=h).status_code == 200


def test_token_cache_key_changes_with_secret():
    """토큰 캐시 분리 (2026-09-05): 시크릿을 바꾸면 옛 토큰을 재사용하지 않는다."""
    from app.services.kis_auth import KisAuth

    a = KisAuth("PS" + "k" * 34, "A" * 180, "prod")
    b = KisAuth("PS" + "k" * 34, "B" * 180, "prod")          # 같은 앱키, 새 시크릿
    assert a._redis_key() != b._redis_key()
    assert KisAuth("PS" + "k" * 34, "A" * 180, "prod")._redis_key() == a._redis_key()
    assert KisAuth("PS" + "k" * 34, "A" * 180, "vps")._redis_key() != a._redis_key()


def test_business_error_in_500_is_not_retried():
    """500 재시도 범위 (2026-09-05): 자격 오류(EGW00304)는 즉시 알리고, 유량(EGW00201)만 재시도한다."""
    import pytest

    from app.services.kis_client import KisClient, KisError

    class _Resp:
        status_code = 500

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Sess:
        def __init__(self, body):
            self.body, self.calls = body, 0

        def get(self, *a, **kw):
            self.calls += 1
            return _Resp(self.body)

    class _Auth:
        base_url = "https://example.invalid"

        def headers(self, tr_id, session=None):
            return {}

    sess = _Sess({"rt_cd": "1", "msg_cd": "EGW00304", "msg1": "고객식별키가 유효하지 않습니다."})
    with pytest.raises(KisError, match="EGW00304"):
        KisClient(_Auth(), sess)._get("/x", "TR", {})
    assert sess.calls == 1  # 재시도 없음

    from app.broker import humanize_kis_error
    assert "앱시크릿" in humanize_kis_error("KIS error EGW00304 고객식별키가 유효하지 않습니다.")



def test_same_app_key_on_two_accounts_hint(monkeypatch):
    """계좌별 앱키 힌트 (2026-09-05): 같은 앱키를 다른 계좌에도 등록한 상태에서 계좌 오류가 나면 원인을 짚어준다."""
    from app.services import kis_client

    class _Fail:
        def __init__(self, *a, **kw):
            pass

        def probe_balance(self, prdt=None):
            raise kis_client.KisError("KIS error rt_cd=2 msg=ERROR : INPUT INVALID_CHECK_ACNO")

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fail)
    c, h = _client()
    key, sec = "PS" + "z" * 34, "S" * 180
    c.post("/broker/accounts", json={"label": "연금", "app_key": key, "app_secret": sec,
                                     "account_no": "10040029-22"}, headers=h)
    other = c.post("/broker/accounts", json={"label": "운영", "app_key": key, "app_secret": sec,
                                             "account_no": "68800037-01"}, headers=h).json()["id"]
    out = c.post(f"/broker/accounts/{other}/test", headers=h).json()
    assert out["ok"] is False and out["suggest"] is None
    assert "'연금'(1004**29-22)" in out["message"] and "계좌 하나에만 유효" in out["message"]



def test_reservation_window_rules():
    """예약주문 접수 창 (KIS 규정): 15:40~다음 영업일 07:30, 23:40~00:10 제외, 휴장일은 종일."""
    from datetime import datetime

    from app.broker import KST, reservation_window

    def at(y, m, d, hh, mm):
        return reservation_window(datetime(y, m, d, hh, mm, tzinfo=KST))

    assert at(2026, 9, 7, 16, 0)["open"] is True      # 월 16:00 — 장 마감 후
    assert at(2026, 9, 7, 7, 0)["open"] is True       # 월 07:00 — 장 시작 전
    assert at(2026, 9, 7, 10, 0)["open"] is False     # 월 10:00 — 장중
    assert at(2026, 9, 7, 15, 39)["open"] is False    # 15:39 — 아직
    assert at(2026, 9, 5, 14, 0)["open"] is True      # 토 14:00 — 휴장일 종일
    assert at(2026, 9, 7, 23, 50)["open"] is False    # 서버 초기화
    assert at(2026, 9, 7, 0, 5)["open"] is False


def _fake_kis(monkeypatch, reserve_fail_for: set[str] | None = None, remote: list[dict] | None = None):
    """예약주문 접수/취소/조회를 흉내내는 클라이언트 — 호출 기록을 남긴다."""
    from app.services import kis_client

    calls: dict = {"reserve": [], "cancel": []}
    seq = {"n": 100}

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def reserve_order(self, code, side, qty, price, end_date=None):
            calls["reserve"].append((code, side, qty, price))
            if reserve_fail_for and code in reserve_fail_for:
                raise kis_client.KisError("KIS error APBK0919 주문가능금액을 초과하였습니다 (HTTP 200)")
            seq["n"] += 1
            return {"rsvn_ord_seq": str(seq["n"]), "msg": "예약주문이 접수되었습니다", "raw": {"RSVN_ORD_SEQ": str(seq["n"])}}

        def cancel_reserved_order(self, rsvn_ord_seq, ord_dt, orgno=""):
            calls["cancel"].append(rsvn_ord_seq)
            return {"msg": "예약주문이 취소되었습니다", "raw": {}}

        def list_reserved_orders(self, start, end, include_cancelled=True):
            return remote or []

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fake)
    return calls


def _plan_setup(c, h, exec_day: str):
    """포트 + 계좌 연결 + 그날의 주문표 스냅샷(그리드 매수 2줄·익절 매도 1줄)을 만든다."""
    from datetime import date as _d

    from app.db import SessionLocal
    from app.models import PortfolioPlan

    pid = c.post("/portfolios", json={"name": "예약테스트"}, headers=h).json()["id"]
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    assert c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h).status_code == 200
    orders = [
        {"instrument": "K200", "side": "buy", "otype": "limit", "qty": 3, "price": 108000, "kind": "grid1"},
        {"instrument": "K200", "side": "buy", "otype": "limit", "qty": 3, "price": 106500, "kind": "grid2"},
        {"instrument": "LEV", "side": "sell", "otype": "market", "qty": 2, "price": None, "kind": "liq"},
    ]
    with SessionLocal() as s:
        s.add(PortfolioPlan(portfolio_id=pid, trade_date=_d.fromisoformat(exec_day),
                            payload={"orders": orders, "regime": "BULL"}))
        s.commit()
    return pid, acct, orders


def test_reserve_orders_flow(monkeypatch):
    """주문표 → 예약주문: 줄 단위 접수·실패 기록, 주문표 불일치 거절, 중복 차단, 취소, 상태 동기화."""
    from datetime import datetime, timedelta

    import app.broker as broker

    c, h = _client()
    # 접수 창을 강제로 열어 둔다(토요일 16:00) — 창 규칙은 별도 테스트
    monkeypatch.setattr(broker, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "test"})
    exec_day = (datetime.now(broker.KST).date() + timedelta(days=1)).isoformat()
    pid, acct, orders = _plan_setup(c, h, exec_day)
    calls = _fake_kis(monkeypatch, reserve_fail_for={"122630"})   # 레버리지 매도는 실패시킨다

    # 화면 줄 그대로 접수
    r = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": orders}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["reserved"] == 2 and out["failed"] == 1
    by = {i["kind"]: i for i in out["items"]}
    assert by["grid1"]["status"] == "reserved" and by["grid1"]["rsvn_ord_seq"] == "101" and by["grid1"]["code"] == "069500"
    assert by["grid2"]["status"] == "reserved" and by["grid2"]["price"] == 106500
    assert by["liq"]["status"] == "failed" and "주문가능금액" in by["liq"]["message"] and by["liq"]["code"] == "122630"
    assert calls["reserve"] == [("069500", "buy", 3, 108000), ("069500", "buy", 3, 106500), ("122630", "sell", 2, None)]

    # 같은 줄 재접수 → 중복(활성 예약 있음), KIS 호출 없음. 실패했던 줄은 재시도됨(여기선 다시 실패)
    r2 = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": orders}, headers=h).json()
    assert [i["status"] for i in r2["items"]] == ["duplicate", "duplicate", "failed"]
    assert len(calls["reserve"]) == 4

    # 화면 주문표가 서버 계획과 다르면(수량 변경) 거절 — KIS 호출 없음
    bad = dict(orders[0], qty=99)
    r3 = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": [bad]}, headers=h).json()
    assert r3["items"][0]["status"] == "mismatch" and r3["reserved"] == 0 and len(calls["reserve"]) == 4

    # 목록: 실행일 필터 + 접수 창
    lst = c.get(f"/portfolio/{pid}/orders?date={exec_day}", headers=h).json()
    assert lst["window"]["open"] is True
    assert sorted(i["status"] for i in lst["items"]) == ["failed", "failed", "reserved", "reserved"]

    # 취소
    grid1 = next(i for i in lst["items"] if i["kind"] == "grid1" and i["status"] == "reserved")
    cx = c.post(f"/portfolio/{pid}/orders/{grid1['id']}/cancel", headers=h)
    assert cx.status_code == 200 and cx.json()["status"] == "cancelled" and calls["cancel"] == ["101"]
    assert c.post(f"/portfolio/{pid}/orders/{grid1['id']}/cancel", headers=h).status_code == 409  # 이미 취소

    # 상태 동기화: KIS 조회에 grid2 가 전량 체결로 나오면 filled
    _fake_kis(monkeypatch, remote=[{"rsvn_ord_seq": "102", "order_no": "0001234", "filled_qty": 3,
                                    "cancel_dt": "", "result": "정상처리"}])
    lst2 = c.get(f"/portfolio/{pid}/orders?date={exec_day}&refresh=1", headers=h).json()
    g2 = next(i for i in lst2["items"] if i["kind"] == "grid2")
    assert g2["status"] == "filled" and g2["order_no"] == "0001234" and g2["filled_qty"] == 3

    # 다른 계정은 접근 불가
    c2, h2 = _client()
    assert c2.get(f"/portfolio/{pid}/orders", headers=h2).status_code == 404
    assert c2.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": orders}, headers=h2).status_code == 404


def test_reserve_orders_guards(monkeypatch):
    """접수 거절 조건: 창 닫힘(409)·연결 계좌 없음(409)·모의투자(409)·스냅샷 없음(404)·지난 실행일(409)."""
    from datetime import datetime, timedelta

    import app.broker as broker

    c, h = _client()
    _fake_kis(monkeypatch)
    exec_day = (datetime.now(broker.KST).date() + timedelta(days=1)).isoformat()
    line = [{"instrument": "K200", "side": "buy", "otype": "limit", "qty": 1, "price": 100000, "kind": "grid1"}]

    pid = c.post("/portfolios", json={"name": "가드"}, headers=h).json()["id"]
    assert c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": line}, headers=h).status_code == 409  # 계좌 없음

    vps = c.post("/broker/accounts", json={"label": "모의", "app_key": "PS" + "v" * 34, "app_secret": "S" * 180,
                                           "account_no": "12345678-01", "env": "vps"}, headers=h).json()
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": vps["id"]}, headers=h)
    r = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": line}, headers=h)
    assert r.status_code == 409 and "모의투자" in r.json()["detail"]

    prod = c.post("/broker/accounts", json={"label": "실전", "app_key": "PS" + "p" * 34, "app_secret": "S" * 180,
                                            "account_no": "12345678-01"}, headers=h).json()
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": prod["id"]}, headers=h)
    monkeypatch.setattr(broker, "reservation_window", lambda now=None, session=None: {"open": False, "reason": "장중"})
    r = c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": line}, headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "장중"

    monkeypatch.setattr(broker, "reservation_window", lambda now=None, session=None: {"open": True, "reason": "ok"})
    assert c.post(f"/portfolio/{pid}/orders/reserve", json={"date": exec_day, "lines": line}, headers=h).status_code == 404  # 스냅샷 없음
    past = (datetime.now(broker.KST).date() - timedelta(days=1)).isoformat()
    assert c.post(f"/portfolio/{pid}/orders/reserve", json={"date": past, "lines": line}, headers=h).status_code == 409


def test_fetch_executions_spans_both_kis_trs():
    """체결 조회 TR 분할 (2026-09-05): 365일 조회가 '3개월 이전' TR 만 타서 최근 체결이 빠지던 결함.
    3개월 경계에 걸치면 recent(TTTC0081R)·old(CTSC9215R) 둘 다 조회하고 합친다."""
    from datetime import date as _d, timedelta

    from app.services.kis_client import KisTradingClient

    class _Auth:
        env = "prod"
        base_url = "https://example.invalid"

        def headers(self, tr_id, session=None):
            return {}

    calls: list[tuple[str, str, str]] = []

    def fake_get(self, path, tr_id, params):
        calls.append((tr_id, params["INQR_STRT_DT"], params["INQR_END_DT"]))
        if tr_id == "TTTC0081R":   # 최근 3개월: 두 달 전 매수 (기존 코드에선 365일 조회에서 빠지던 건)
            d = (_d.today() - timedelta(days=60)).strftime("%Y%m%d")
            return {"output1": [{"ord_dt": d, "pdno": "005930", "sll_buy_dvsn_cd": "02", "odno": "A1",
                                 "tot_ccld_qty": "11", "avg_prvs": "72150", "ord_qty": "11", "rmn_qty": "0",
                                 "prdt_name": "삼성전자"}], "ctx_area_nk100": ""}
        d = (_d.today() - timedelta(days=200)).strftime("%Y%m%d")
        return {"output1": [{"ord_dt": d, "pdno": "069500", "sll_buy_dvsn_cd": "02", "odno": "B1",
                             "tot_ccld_qty": "4", "avg_prvs": "108555", "ord_qty": "4", "rmn_qty": "0",
                             "prdt_name": "KODEX 200"}], "ctx_area_nk100": ""}

    c = KisTradingClient(_Auth(), cano="12345678", acnt_prdt_cd="01")
    c._get = fake_get.__get__(c)  # type: ignore[method-assign]
    c._throttle = lambda: None      # type: ignore[method-assign]

    today = _d.today()
    # 365일: 두 TR 모두, 구간이 3개월 경계에서 나뉜다
    ex = c.fetch_executions(today - timedelta(days=364), today)
    assert [t for t, *_ in calls] == ["TTTC0081R", "CTSC9215R"]
    boundary = (today - timedelta(days=89)).strftime("%Y%m%d")
    assert calls[0][1] == boundary and calls[1][2] == (today - timedelta(days=90)).strftime("%Y%m%d")
    assert [(e.code, e.filled_qty) for e in ex] == [("069500", 4), ("005930", 11)]  # 날짜순, 두 구간 합침
    # 30일: recent 만
    calls.clear()
    c.fetch_executions(today - timedelta(days=29), today)
    assert [t for t, *_ in calls] == ["TTTC0081R"]
    # 200일 전 하루: old 만
    calls.clear()
    c.fetch_executions(today - timedelta(days=200), today - timedelta(days=199))
    assert [t for t, *_ in calls] == ["CTSC9215R"]
