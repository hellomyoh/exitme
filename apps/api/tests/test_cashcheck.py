"""계좌 예수금 연동 (2026-09-06 지시) — 시작 시 잔고 불러오기·시작과 함께 계좌 연결·장 마감 예수금 대조·차액 보정."""
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
    tok = c.post("/auth/register", json={"email": f"cc{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _acct(c, h, env="prod"):
    return c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "x" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01", "env": env}, headers=h).json()


class FakeBal:
    """fetch_balance / fetch_executions 만 흉내 — 네트워크 없음. holdings: {code: (qty, avg_price)}."""

    def __init__(self, deposit: int, d2: int, holdings: dict[str, tuple[int, int]]):
        self.deposit, self.d2, self.holdings = deposit, d2, holdings

    def fetch_balance(self):
        rows = [{"code": k, "name": {"069500": "KODEX 200", "005930": "삼성전자"}.get(k, k), "qty": q, "avg_price": p,
                 "buy_amount": q * p, "price": p, "eval_amount": q * p} for k, (q, p) in self.holdings.items()]
        return {"holdings": rows, "deposit": self.deposit, "deposit_d1": self.d2, "deposit_d2": self.d2,
                "total_eval": self.deposit + sum(r["eval_amount"] for r in rows)}

    def fetch_executions(self, start, end, only_filled=True):
        return []


def test_fetch_balance_parses_d1_d2_deposit():
    """KIS 잔고 요약(output2)의 예수금총액·익일정산·가수도정산(D+2)을 각각 돌려준다 — 원장 현금과 같은 정의는 D+2."""
    from app.services.kis_client import KisTradingClient

    class _Auth:
        env = "prod"
        base_url = "https://example.invalid"

        def headers(self, tr_id, session=None):
            return {}

    def fake_get(self, path, tr_id, params):
        return {"output1": [{"pdno": "069500", "prdt_name": "KODEX 200", "hldg_qty": "10", "pchs_avg_pric": "100000",
                             "pchs_amt": "1000000", "prpr": "101000", "evlu_amt": "1010000"}],
                "output2": [{"dnca_tot_amt": "4200000", "nxdy_excc_amt": "4100000", "prvs_rcdl_excc_amt": "3998500",
                             "tot_evlu_amt": "5008500"}], "ctx_area_nk100": ""}

    c = KisTradingClient(_Auth(), cano="12345678", acnt_prdt_cd="01")
    c._get = fake_get.__get__(c)  # type: ignore[method-assign]
    c._throttle = lambda: None      # type: ignore[method-assign]
    b = c.fetch_balance()
    assert b["deposit"] == 4_200_000 and b["deposit_d1"] == 4_100_000 and b["deposit_d2"] == 3_998_500
    assert b["holdings"] == [{"code": "069500", "name": "KODEX 200", "qty": 10, "avg_price": 100000, "buy_amount": 1000000,
                              "price": 101000, "eval_amount": 1010000}]
    # D+2 필드가 없는 응답 → 총액으로 채움
    def fake_get_old(self, path, tr_id, params):
        return {"output1": [], "output2": [{"dnca_tot_amt": "700000"}], "ctx_area_nk100": ""}
    c._get = fake_get_old.__get__(c)  # type: ignore[method-assign]
    assert c.fetch_balance()["deposit_d2"] == 700_000


@db
def test_account_balance_view_and_isolation(monkeypatch):
    """시작 패널 '계좌에서 불러오기': D+2 예수금·전략 종목 표시(앞에 정렬), 다른 사용자의 계좌는 404."""
    import app.broker as br

    c, h = _client()
    a = _acct(c, h)
    monkeypatch.setattr(br, "_client", lambda cred: FakeBal(4_000_000, 3_950_000, {"005930": (5, 70000), "069500": (10, 100000)}))
    r = c.get(f"/broker/accounts/{a['id']}/balance?market=KR", headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["deposit_d2"] == 3_950_000 and j["deposit"] == 4_000_000 and j["strategy_count"] == 1
    assert [x["code"] for x in j["holdings"]] == ["069500", "005930"] and j["holdings"][0]["strategy"] is True and j["holdings"][1]["strategy"] is False
    _, h2 = _client()
    assert c.get(f"/broker/accounts/{a['id']}/balance", headers=h2).status_code == 404
    # 조회 실패 → 502 + 안내 문구
    class _Boom(FakeBal):
        def fetch_balance(self):
            raise RuntimeError("KIS error EGW00103 앱키 오류")
    monkeypatch.setattr(br, "_client", lambda cred: _Boom(0, 0, {}))
    r = c.get(f"/broker/accounts/{a['id']}/balance", headers=h)
    assert r.status_code == 502 and "증권사 조회 실패" in r.json()["detail"]


@db
def test_create_portfolio_links_account():
    """시작 요청의 credential_id 로 계좌가 함께 연결된다 — 다른 사용자의 계좌는 404(포트도 만들지 않음)."""
    c, h = _client()
    a = _acct(c, h)
    r = c.post("/portfolios", json={"name": "연동시작", "market": "KR", "code_200": "069500", "credential_id": a["id"]}, headers=h)
    assert r.status_code == 201 and r.json()["linked"] is True
    assert c.get(f"/portfolio/{r.json()['id']}/broker", headers=h).json()["linked"] is True
    _, h2 = _client()
    before = len(c.get("/portfolios", headers=h2).json()["items"])
    assert c.post("/portfolios", json={"name": "x", "credential_id": a["id"]}, headers=h2).status_code == 404
    assert len(c.get("/portfolios", headers=h2).json()["items"]) == before
    # credential_id 없이도 그대로 동작
    assert c.post("/portfolios", json={"name": "단독", "market": "KR"}, headers=h).json()["linked"] is False


@db
def test_cash_check_refresh_tolerance_warn_and_align(monkeypatch):
    """예수금 대조: 허용 오차 안(수수료 범위)은 경고 없음, 넘으면 warn → 차액 등록으로 원장 현금이 계좌와 같아짐. 낡은 결과로는 보정 거절."""
    import app.broker as br

    c, h = _client()
    a = _acct(c, h)
    pid = c.post("/portfolios", json={"name": "대조", "market": "KR", "code_200": "069500", "credential_id": a["id"]}, headers=h).json()["id"]
    day = (datetime.now(KST).date() - timedelta(days=3)).isoformat()
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 5_000_000, "executed_at": day + "T15:30:00+09:00"}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 100_000, "executed_at": day + "T15:31:00+09:00"}, headers=h)
    assert c.get(f"/portfolio/{pid}/cash-check", headers=h).json()["cash_check"] is None
    # 원장 현금 4,000,000 · 계좌 D+2 3,998,500 → 차이 −1,500: 허용 오차(1만원·총자산 0.1%) 안 → warn False
    monkeypatch.setattr(br, "_client", lambda cred: FakeBal(4_200_000, 3_998_500, {"069500": (10, 100000)}))
    j = c.get(f"/portfolio/{pid}/cash-check?refresh=true", headers=h).json()["cash_check"]
    assert j["ledger_cash"] == 4_000_000 and j["account_cash"] == 3_998_500 and j["account_deposit"] == 4_200_000
    assert j["diff"] == -1_500 and j["warn"] is False and j["tolerance"] == 10_000
    # 계좌 3,900,000 → 차이 −100,000 → warn, 연결 상태 응답에도 실림
    monkeypatch.setattr(br, "_client", lambda cred: FakeBal(3_900_000, 3_900_000, {}))
    j = c.get(f"/portfolio/{pid}/cash-check?refresh=true", headers=h).json()["cash_check"]
    assert j["diff"] == -100_000 and j["warn"] is True
    assert c.get(f"/portfolio/{pid}/broker", headers=h).json()["cash_check"]["warn"] is True
    # 차액 등록 → 출금 100,000 → 원장 현금 3,900,000, 저장된 대조 결과 diff 0
    r = c.post(f"/portfolio/{pid}/cash-check/align", headers=h)
    assert r.status_code == 201
    out = r.json()
    assert out["added"] is True and out["kind"] == "withdraw" and out["amount"] == 100_000
    assert out["cash_check"]["diff"] == 0 and out["cash_check"]["warn"] is False and out["cash_check"]["aligned_amount"] == -100_000
    assert c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()["cash"] == 3_900_000
    tx = c.get(f"/portfolio/transactions?portfolio_id={pid}", headers=h).json()["items"][0]
    assert tx["kind"] == "withdraw" and tx["amount"] == 100_000 and "예수금 대조 보정" in tx["memo"] and tx["tags"] == ["cash_check"]
    # 차이 0 이면 등록하지 않음
    assert c.post(f"/portfolio/{pid}/cash-check/align", headers=h).json()["added"] is False
    # 대조 뒤 원장이 바뀌면 낡은 차액으로 보정하지 않는다 (409)
    monkeypatch.setattr(br, "_client", lambda cred: FakeBal(3_850_000, 3_850_000, {}))
    assert c.get(f"/portfolio/{pid}/cash-check?refresh=true", headers=h).json()["cash_check"]["diff"] == -50_000
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000, "executed_at": datetime.now(KST).isoformat()}, headers=h)
    r = c.post(f"/portfolio/{pid}/cash-check/align", headers=h)
    assert r.status_code == 409 and "원장이 바뀌었습니다" in r.json()["detail"]
    # 계좌에 더 있으면(분배금·앱 밖 입금) 입금으로 보정
    monkeypatch.setattr(br, "_client", lambda cred: FakeBal(3_930_000, 3_930_000, {}))
    c.get(f"/portfolio/{pid}/cash-check?refresh=true", headers=h)
    out = c.post(f"/portfolio/{pid}/cash-check/align", headers=h).json()
    assert out["kind"] == "deposit" and out["amount"] == 20_000
    assert c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()["cash"] == 3_930_000


@db
def test_cash_check_guards():
    """대조 결과 없이 보정 → 409, 계좌 미연결 refresh → 409, 미국 포트 refresh → 409."""
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "가드", "market": "KR"}, headers=h).json()["id"]
    assert c.post(f"/portfolio/{pid}/cash-check/align", headers=h).status_code == 409
    assert c.get(f"/portfolio/{pid}/cash-check?refresh=true", headers=h).status_code == 409
    a = _acct(c, h)
    us = c.post("/portfolios", json={"name": "미국", "market": "US", "credential_id": a["id"]}, headers=h).json()["id"]
    r = c.get(f"/portfolio/{us}/cash-check?refresh=true", headers=h)
    assert r.status_code == 409 and "국내 포트만" in r.json()["detail"]


@db
def test_post_close_sync_records_cash_check(monkeypatch):
    """15:45 동기화가 체결 가져오기 뒤 예수금 대조를 저장한다 — 경고·기록만, 원장은 바꾸지 않고 무인 실행도 정지하지 않는다."""
    import app.broker as br

    c, h = _client()
    a = _acct(c, h)
    pid = c.post("/portfolios", json={"name": "동기화", "market": "KR", "code_200": "069500", "credential_id": a["id"]}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": (datetime.now(KST).date() - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)
    monkeypatch.setattr(br, "_client", lambda cred: FakeBal(1_000_000, 980_000, {}))
    with SessionLocal() as s:
        out = br.run_post_close_sync(s, now=datetime.now(KST))
    rec = next(r for r in out["portfolios"] if r["portfolio_id"] == pid)
    assert rec["cash_check"]["ledger_cash"] == 1_000_000 and rec["cash_check"]["diff"] == -20_000 and rec["cash_check"]["warn"] is True
    bk = c.get(f"/portfolio/{pid}/broker", headers=h).json()
    assert bk["cash_check"]["diff"] == -20_000
    assert c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()["cash"] == 1_000_000   # 원장 불변
    assert c.get(f"/portfolio/{pid}/auto-exec", headers=h).json()["paused"] is False                 # 정지 없음


@db
def test_post_close_message_counts_today_and_quiet_retry(monkeypatch):
    """장 마감 동기화 문구 (2026-09-09 사용자 지적 "오늘 체결이 없는데 체결 1건 조회") — 조회 창(어제~오늘)의 어제 건은 '어제분 n건'으로 구분하고
    오늘분만 '오늘 체결'. 17:10 재시도(retry=True)는 신규 등록·상태 갱신·경고·오류가 없으면 로그만 남기고 알림은 보내지 않는다."""
    import app.broker as br
    import app.notify as nt
    from app.services.kis_client import Execution

    yesterday = datetime.now(KST).date() - timedelta(days=1)

    class FakeExec(FakeBal):
        def fetch_executions(self, start, end, only_filled=True):
            return [Execution(order_no="0001", trade_date=yesterday, code="069500", side="buy", filled_qty=1, avg_price=10_000,
                              order_qty=1, remain_qty=0, name="KODEX 200")]

    c, h = _client()
    a = _acct(c, h)
    pid = c.post("/portfolios", json={"name": "문구", "market": "KR", "code_200": "069500", "credential_id": a["id"]}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": (yesterday - timedelta(days=1)).isoformat() + "T15:30:00+09:00"}, headers=h)
    monkeypatch.setattr(br, "_client", lambda cred: FakeExec(990_000, 990_000, {"069500": (1, 10_000)}))   # 체결 뒤 원장 현금 990,000 = 계좌
    sent_all: list[tuple] = []
    monkeypatch.setattr(nt, "notify_event", lambda s, uid, kind, text, level, pf_id: sent_all.append((kind, text, pf_id)) or True)
    sent = lambda: [(k, t) for k, t, p in sent_all if p == pid]   # noqa: E731 — 동기화는 DB 의 모든 연결 포트를 돌므로 이 포트만
    # 15:45 본 실행 — 어제 체결 1건이 처음 등록된다(어제 동기화가 없었던 상황) → 알림
    with SessionLocal() as s:
        rec = next(r for r in br.run_post_close_sync(s, now=datetime.now(KST), only_portfolio_ids={pid})["portfolios"] if r["portfolio_id"] == pid)
    assert rec["today"] == {"fetched": 0, "added": 0} and rec["prev"] == {"fetched": 1, "added": 1} and rec["added"] == 1
    assert [k for k, _t in sent()] == ["sync.post_close"] and "오늘 체결 0건 · 신규 1건 등록 · 어제분 1건 중 1건 새로 등록" in sent()[0][1]
    # 17:10 재시도 — 같은 건이 '이미 등록'이라 변경 없음 → 로그는 남고 알림은 없다
    sent_all.clear()
    with SessionLocal() as s:
        rec2 = next(r for r in br.run_post_close_sync(s, now=datetime.now(KST), retry=True, only_portfolio_ids={pid})["portfolios"] if r["portfolio_id"] == pid)
    assert rec2["added"] == 0 and rec2["prev"] == {"fetched": 1, "added": 0} and sent() == []
    logs = [i for i in c.get(f"/logs?type=event&portfolio_id={pid}", headers=h).json()["items"] if i["kind"] == "sync.post_close"]
    rt = next(i for i in logs if "재시도" in i["text"])   # 두 실행이 같은 초에 기록되면 정렬이 불안정 — 문구로 찾는다
    assert len(logs) == 2 and "오늘 체결 0건 · 신규 0건 등록 · 어제분 1건 이미 등록" in rt["text"] and "변경 없음(알림 생략)" in rt["text"]
    # 재시도라도 무언가 바뀌면(여기서는 예수금 경고) 알림을 보낸다
    monkeypatch.setattr(br, "_client", lambda cred: FakeExec(900_000, 900_000, {"069500": (1, 10_000)}))
    with SessionLocal() as s:
        br.run_post_close_sync(s, now=datetime.now(KST), retry=True, only_portfolio_ids={pid})
    assert any(k == "sync.post_close" for k, _t in sent())
