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
    assert d["holding"] == {"qty": 5, "avg_price": 12000}

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
