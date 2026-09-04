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
    """다종목 (0015): 종목별 FIFO 독립·종목별 보유 초과 매도 거부·overview holdings."""
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
    ov = c.get("/mjournals/overview", headers=h).json()["items"]
    me = next(x for x in ov if x["id"] == jid)
    assert me["holdings"][0]["symbol"] == "대원제약" and me["series"][0]["value"] == 500
