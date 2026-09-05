"""수동 매매일지 — FIFO 계산·요율·소유 격리 (2026-09-05)."""
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"mj{uuid.uuid4().hex[:8]}@x.dev",
                                         "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_journal_fifo_and_summary():
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "대원제약 스윙", "symbol": "대원제약",
                                     "broker": "NH", "fee_rate": 0.001, "tax_rate": 0.002},
                 headers=h).json()["id"]
    # 매수 10@10,000(1/5) → 매수 10@12,000(1/10) → 매도 15@13,000(2/1)
    for side, qty, price, d in [("buy", 10, 10000, "2026-01-05"), ("buy", 10, 12000, "2026-01-10"),
                                ("sell", 15, 13000, "2026-02-01")]:
        r = c.post(f"/mjournals/{jid}/entries",
                   json={"side": side, "qty": qty, "price": price, "trade_date": d}, headers=h)
        assert r.status_code == 201, r.text
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    sell = next(r for r in d["rows"] if r["side"] == "sell")
    # FIFO 원가 = 10×10,000 + 5×12,000 = 160,000 / 매도금 195,000 / 매도비용 = 195,000×0.003 = 585
    assert sell["amount"] == 195000 and sell["cost"] == 585
    assert sell["realized"] == 195000 - 585 - 160000
    assert sell["buy_date"] == "2026-01-05" and sell["hold_days"] == 27
    assert abs(sell["return_pct"] - sell["realized"] / 160000) < 1e-9
    s = d["summary"]
    assert s["buy_amount"] == 220000 and s["sell_amount"] == 195000
    assert s["cost"] == round(100000 * 0.001) + round(120000 * 0.001) + 585
    assert s["realized"] == sell["realized"]
    assert d["holdings"] == [{"symbol": "대원제약", "qty": 5, "avg_price": 12000, "cost": 60000,
                              "realized": 34415, "matched": 160000, "return_pct": 34415 / 160000}]
    assert s["return_pct"] == 34415 / 160000  # 요약 수익률 = 실현손익 ÷ 매도분 원가 (2026-09-05)

    # 보유 초과 매도 거부
    r = c.post(f"/mjournals/{jid}/entries",
               json={"side": "sell", "qty": 99, "price": 13000, "trade_date": "2026-02-02"}, headers=h)
    assert r.status_code == 422


def test_journal_isolation_and_delete():
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "A", "symbol": "X"}, headers=h).json()["id"]
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 1, "price": 100}, headers=h)
    _, h2 = _client()
    assert c.get(f"/mjournals/{jid}", headers=h2).status_code == 404
    assert c.delete(f"/mjournals/{jid}", headers=h2).status_code == 404
    assert c.get("/mjournals", headers=h2).json()["items"] == []
    # 소유자 삭제 → CASCADE
    assert c.delete(f"/mjournals/{jid}", headers=h).json()["deleted"] is True
    assert c.get("/mjournals", headers=h).json()["items"] == []


def test_multi_symbol_fifo_isolation():
    """다종목 (0015): 종목별 FIFO 독립·종목별 보유 초과 매도 거부·종목별 누적 실현 시리즈."""
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "멀티", "symbol": "대원제약",
                                     "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    E = lambda b: c.post(f"/mjournals/{jid}/entries", json=b, headers=h)
    assert E({"side": "buy", "qty": 10, "price": 1000, "trade_date": "2026-01-01"}).status_code == 201  # 기본 종목
    assert E({"side": "buy", "qty": 5, "price": 2000, "trade_date": "2026-01-02", "symbol": "휴메딕스"}).status_code == 201
    # 휴메딕스 보유 5 — 대원제약 10주가 있어도 휴메딕스 6주 매도는 거부 (종목별 검사)
    assert E({"side": "sell", "qty": 6, "price": 2100, "trade_date": "2026-01-03", "symbol": "휴메딕스"}).status_code == 422
    assert E({"side": "sell", "qty": 5, "price": 2100, "trade_date": "2026-01-03", "symbol": "휴메딕스"}).status_code == 201
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    sell = next(r for r in d["rows"] if r["side"] == "sell")
    assert sell["symbol"] == "휴메딕스" and sell["realized"] == 5 * (2100 - 2000)  # 대원제약 로트와 안 섞임
    assert d["holdings"] == [{"symbol": "대원제약", "qty": 10, "avg_price": 1000, "cost": 10000,
                              "realized": 0, "matched": 0, "return_pct": None}]
    assert set(d["symbols"]) == {"대원제약", "휴메딕스"}
    assert d["series"] == {"휴메딕스": [{"date": "2026-01-03", "value": 500}]}  # 매도 없는 종목은 라인 없음


def test_journal_isolation_between_journals_and_users():
    """일지 간·계정 간 완전 분리 (2026-09-05 지시): 새 일지에는 다른 일지의 종목·보유·추이가 섞이지 않고,
    전 일지 합산 엔드포인트도 없다. 다른 계정은 일지 자체를 볼 수 없다."""
    c, h = _client()
    a = c.post("/mjournals", json={"name": "A", "symbol": "tiger 200", "fee_rate": 0.0, "tax_rate": 0.0},
               headers=h).json()["id"]
    c.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 10, "price": 1000, "trade_date": "2026-01-01"}, headers=h)
    c.post(f"/mjournals/{a}/entries", json={"side": "sell", "qty": 4, "price": 1500, "trade_date": "2026-01-02"}, headers=h)
    b = c.post("/mjournals", json={"name": "B", "symbol": "kodex 200"}, headers=h).json()["id"]
    db = c.get(f"/mjournals/{b}", headers=h).json()
    assert db["symbols"] == ["kodex 200"] and db["holdings"] == [] and db["series"] == {} and db["rows"] == []
    assert c.get("/mjournals/overview", headers=h).status_code in (404, 422)  # 합산 뷰 제거

    c2, h2 = _client()  # 다른 계정
    assert c2.get(f"/mjournals/{a}", headers=h2).status_code == 404
    assert c2.get("/mjournals", headers=h2).json()["items"] == []
    assert c2.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 1, "price": 1}, headers=h2).status_code == 404


def test_journal_broker_link_and_import(monkeypatch):
    """일지 ↔ 계좌 연결 + 체결 가져오기 (0018): 종목명 정규화 매칭·새 종목 추가·보유 초과/수동 중복 경고·멱등·격리."""
    from datetime import date as _d

    from app.services import kis_client
    from app.services.kis_client import Execution

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "연금저축", "symbol": "kodex 200", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    # 수동 기초 보유 10주
    assert c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 10, "price": 10000,
                                                     "trade_date": "2026-01-02"}, headers=h).status_code == 201
    # 연결 전 가져오기는 409
    assert c.post(f"/mjournals/{jid}/import-fills", headers=h).status_code == 409

    acct = c.post("/broker/accounts", json={"label": "연금", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "10040029-22"}, headers=h).json()
    assert c.get(f"/mjournals/{jid}/broker", headers=h).json() == {"linked": False, "account": None}
    r = c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h).json()
    assert r["linked"] is True and r["account"]["label"] == "연금" and r["account"]["account_no"] == "1004**29-22"
    assert c.get(f"/mjournals/{jid}", headers=h).json()["linked_account"]["id"] == acct["id"]
    # 다른 계정: 일지도 계좌도 보이지 않는다
    c2, h2 = _client()
    assert c2.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h2).status_code == 404
    j2 = c2.post("/mjournals", json={"name": "남의 일지", "symbol": "x"}, headers=h2).json()["id"]
    assert c2.put(f"/mjournals/{j2}/broker", json={"credential_id": acct["id"]}, headers=h2).status_code == 404

    def ex(no, d, code, side, qty, price, name):
        return Execution(order_no=no, trade_date=d, code=code, side=side, filled_qty=qty, avg_price=price,
                         order_qty=qty, remain_qty=0, name=name)

    execs = [
        ex("A1", _d(2026, 1, 5), "069500", "buy", 5, 11000, "KODEX 200"),    # 이름 매칭(대소문자·공백 무시) → 코드 학습
        ex("A2", _d(2026, 1, 6), "102110", "buy", 3, 20000, "TIGER 200"),    # 새 종목
        ex("A3", _d(2026, 1, 7), "069500", "sell", 30, 12000, "KODEX 200"),  # 보유 25 초과 → 경고(등록은 됨)
        ex("A4", _d(2026, 1, 2), "069500", "buy", 10, 10000, "KODEX 200"),   # 수동 기록과 동일 → 중복 경고
    ]

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def fetch_executions(self, start, end, only_filled=True):
            return execs

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fake)

    pv = c.post(f"/mjournals/{jid}/import-fills?days=30", headers=h).json()
    assert pv["dry_run"] is True and pv["fetched"] == 4 and pv["added"] == 0
    by = {i["broker_ref"]: i for i in pv["items"]}
    assert by["A4:2026-01-02"]["symbol"] == "kodex 200" and by["A4:2026-01-02"]["match"] == "이름"
    assert by["A1:2026-01-05"]["symbol"] == "kodex 200" and by["A1:2026-01-05"]["match"] == "코드"
    assert by["A2:2026-01-06"]["symbol"] == "TIGER 200" and by["A2:2026-01-06"]["match"] == "새 종목"
    assert any("보유(25주)보다 많은 매도" in w for w in by["A3:2026-01-07"]["warnings"])
    assert any("수동 기록" in w for w in by["A4:2026-01-02"]["warnings"])
    assert pv["new_symbols"] == ["TIGER 200"]
    assert len(c.get(f"/mjournals/{jid}", headers=h).json()["rows"]) == 1  # 미리보기는 저장하지 않는다

    ap = c.post(f"/mjournals/{jid}/import-fills?days=30&dry_run=false", headers=h).json()
    assert ap["added"] == 4 and ap["skipped"] == 0
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    assert len(d["rows"]) == 5 and sum(r["source"] == "broker" for r in d["rows"]) == 4
    assert set(d["symbols"]) == {"kodex 200", "TIGER 200"}
    over = next(r for r in d["rows"] if r["side"] == "sell")
    assert "많은 매도" in (over.get("error") or "") and over["code"] == "069500"  # 경고 행으로 표시(자동 수정 없음)

    again = c.post(f"/mjournals/{jid}/import-fills?days=30&dry_run=false", headers=h).json()
    assert again["added"] == 0 and again["skipped"] == 4                          # 재실행 멱등
    assert len(c.get(f"/mjournals/{jid}", headers=h).json()["rows"]) == 5

    c.put(f"/mjournals/{jid}/broker", json={"credential_id": None}, headers=h)   # 해제
    assert c.get(f"/mjournals/{jid}/broker", headers=h).json()["linked"] is False
    assert c.post(f"/mjournals/{jid}/import-fills", headers=h).status_code == 409



def test_journal_close_reopen_and_dashboard_assets():
    """청산(0020): 청산 일지는 기록 추가 409·대시보드 매매일지 자산·총자산에서 제외, 다시 열기로 복구.
    대시보드는 주식 거래 자산(trading_total)과 매매일지 자산(journal)을 분리해 내려준다."""
    c, h = _client()
    a = c.post("/mjournals", json={"name": "연금", "symbol": "kodex 200", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    b = c.post("/mjournals", json={"name": "정리끝", "symbol": "tiger 200", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    c.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 10, "price": 100000, "trade_date": "2026-01-02"}, headers=h)
    c.post(f"/mjournals/{b}/entries", json={"side": "buy", "qty": 5, "price": 50000, "trade_date": "2026-01-02"}, headers=h)
    c.post(f"/mjournals/{b}/entries", json={"side": "sell", "qty": 5, "price": 60000, "trade_date": "2026-01-03"}, headers=h)

    d = c.get("/dashboard", headers=h).json()
    assert d["journal"] == 1_000_000 and d["trading_total"] == d["stock"] + d["cash"]
    assert d["total"] == d["stock"] + d["cash"] + d["other"] + d["journal"]
    assert {j["name"] for j in d["journals"]} == {"연금", "정리끝"}
    done = next(j for j in d["journals"] if j["name"] == "정리끝")
    assert done["cost"] == 0 and done["realized"] == 50_000 and done["counted"] is True

    # 청산 → 대시보드에서 사라짐, 기록 추가 거절, 목록/상세에 closed_at
    r = c.post(f"/mjournals/{b}/close", headers=h).json()
    assert r["closed_at"] and r["warning"] is None            # 보유 없음 → 경고 없음
    assert c.post(f"/mjournals/{b}/entries", json={"side": "buy", "qty": 1, "price": 1}, headers=h).status_code == 409
    d2 = c.get("/dashboard", headers=h).json()
    assert {j["name"] for j in d2["journals"]} == {"연금"} and d2["journal"] == 1_000_000
    assert next(i for i in c.get("/mjournals", headers=h).json()["items"] if i["id"] == b)["closed_at"]
    assert c.get(f"/mjournals/{b}", headers=h).json()["closed_at"]
    # 보유가 남은 일지를 청산하면 경고 문구
    assert "보유 잔여" in (c.post(f"/mjournals/{a}/close", headers=h).json()["warning"] or "")
    assert c.get("/dashboard", headers=h).json()["journal"] == 0
    # 다시 열기
    assert c.post(f"/mjournals/{a}/reopen", headers=h).json()["closed_at"] is None
    assert c.get("/dashboard", headers=h).json()["journal"] == 1_000_000
    assert c.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 1, "price": 1000}, headers=h).status_code == 201


def test_journal_same_account_as_portfolio_not_double_counted():
    """같은 증권사 계좌가 실전매매 포트에도 연결돼 있으면 매매일지 자산은 총자산에 넣지 않는다(counted=False)."""
    c, h = _client()
    acct = c.post("/broker/accounts", json={"label": "연금", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "10040029-22"}, headers=h).json()
    pid = c.post("/portfolios", json={"name": "연금 포트"}, headers=h).json()["id"]
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h)
    jid = c.post("/mjournals", json={"name": "연금 일지", "symbol": "kodex 200", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h)
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 2, "price": 100000, "trade_date": "2026-01-02"}, headers=h)
    d = c.get("/dashboard", headers=h).json()
    j = next(x for x in d["journals"] if x["id"] == jid)
    assert j["counted"] is False and "같은 증권사 계좌" in j["note"] and j["cost"] == 200_000
    assert d["journal"] == 0  # 표시는 하되 총자산에는 미포함


def test_reset_assets_wipes_everything_but_account():
    """계정 자산 전체 초기화 (2026-09-05 지시): 포트·일지·기타 자산·스냅샷 삭제, 아이디 확인 불일치는 400, 계좌·자격은 유지."""
    import uuid as _u

    c = TestClient(app, base_url="https://testserver")
    email = f"rs{_u.uuid4().hex[:8]}@x.dev"
    tok = c.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid = c.post("/portfolios", json={"name": "포트"}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": "2026-01-02T10:00:00+09:00"}, headers=h)
    jid = c.post("/mjournals", json={"name": "일지", "symbol": "x"}, headers=h).json()["id"]
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 1, "price": 1000}, headers=h)
    acct = c.post("/broker/accounts", json={"label": "계좌", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "12345678-01"}, headers=h).json()
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h)
    assert c.get("/dashboard", headers=h).status_code == 200  # 스냅샷 생성

    assert c.post("/account/reset-assets", json={"confirm": "wrong"}, headers=h).status_code == 400
    assert c.post("/account/reset-assets", json={"confirm": email, "scopes": []}, headers=h).status_code == 422
    out = c.post("/account/reset-assets", json={"confirm": email}, headers=h).json()
    assert out["reset"] is True and out["deleted"]["portfolios"] >= 1 and out["deleted"]["journals"] == 1
    assert out["deleted"]["snapshots"] >= 1
    assert c.get("/portfolios", headers=h).json()["items"] == []
    assert c.get("/mjournals", headers=h).json()["items"] == []
    assert c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).status_code == 404
    # 계정·증권사 계좌 자격은 남는다
    assert c.get("/auth/me", headers=h).status_code == 200
    assert len(c.get("/broker/accounts", headers=h).json()["items"]) == 1
    # 이후 대시보드는 빈 상태로 다시 시작
    d = c.get("/dashboard", headers=h).json()
    assert d["total"] == 0 and d["journals"] == [] and (d.get("portfolios") or []) == []
