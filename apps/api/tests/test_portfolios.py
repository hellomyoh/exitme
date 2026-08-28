"""실전매매 기록 테스트 — FIFO·XIRR·TWR·암호화·격리 (feature-portfolio §12)."""
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.main import app
from app.portfolios import twr, xirr

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False


# ── 수익률 수학 (DB 불필요)
def test_xirr_hand_calc():
    # 100 투자 → 1년 뒤 110 회수: XIRR = 10%
    r = xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), 110.0)])
    assert r == pytest.approx(0.10, abs=1e-4)


def test_xirr_two_deposits():
    # 100 + 6개월 뒤 100 투자, 1년 시점 평가 220
    r = xirr([(date(2025, 1, 1), -100.0), (date(2025, 7, 2), -100.0), (date(2026, 1, 1), 220.0)])
    assert r is not None and 0.10 < r < 0.20


def test_twr_ignores_flows():
    # 100→110 (+10%), 입금 100 후 210→231 (+10%) → TWR = 21%
    daily = [(date(2025, 1, 1), 100.0, 0.0), (date(2025, 1, 2), 110.0, 0.0),
             (date(2025, 1, 3), 210.0, 100.0), (date(2025, 1, 4), 231.0, 0.0)]
    assert twr(daily) == pytest.approx(0.21, abs=1e-9)


def test_crypto_roundtrip():
    from app.crypto import decrypt_int, encrypt_int

    for v in (0, 1, 70000, 10**14):
        token = encrypt_int(v)
        assert decrypt_int(token) == v
    # 평문 미노출 — 짧은 수는 base64 우연 일치가 가능하므로 긴 값으로 검사
    assert "123456789012345" not in encrypt_int(123456789012345)


pytestmark_db = pytest.mark.skipif(not DB_UP, reason="database not reachable")


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
class TestPortfolioApi:
    @pytest.fixture(autouse=True)
    def seeded(self):
        from tests.test_backtest_api import seed_synthetic

        with SessionLocal() as s:
            seed_synthetic(s, "069500", "KODEX 200")

    def _client_token(self):
        client = TestClient(app, base_url="https://testserver")
        email = f"pf{uuid.uuid4().hex[:8]}@stocklab.dev"
        token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
        return client, {"Authorization": f"Bearer {token}"}

    def test_fifo_realized_hand_calc(self):
        client, h = self._client_token()
        ts = datetime(2025, 1, 10, 10, 0, tzinfo=timezone.utc)
        for qty, price in ((100, 70000), (100, 68000)):
            r = client.post("/positions", json={"kind": "buy", "code": "069500", "qty": qty, "price": price,
                                                "executed_at": ts.isoformat()}, headers=h)
            assert r.status_code == 201
        # 150주 매도 @71000 — FIFO: 100×(71000-70000) + 50×(71000-68000) = 100,000 + 150,000
        r = client.post("/positions", json={"kind": "sell", "code": "069500", "qty": 150, "price": 71000,
                                            "executed_at": ts.isoformat()}, headers=h)
        assert r.status_code == 201
        assert r.json()["realized_pnl"] == 100 * 1000 + 50 * 3000
        # 잔여 로트: 50주 @68000
        s = client.get("/portfolio/summary", headers=h).json()
        pos = [p for p in s["positions"] if p["code"] == "069500"][0]
        assert pos["qty"] == 50 and pos["avg_price"] == 68000
        assert s["realized_pnl"] == 250_000

    def test_oversell_rejected(self):
        client, h = self._client_token()
        ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
        client.post("/positions", json={"kind": "buy", "code": "069500", "qty": 10, "price": 70000,
                                        "executed_at": ts}, headers=h)
        r = client.post("/positions", json={"kind": "sell", "code": "069500", "qty": 11, "price": 70000,
                                            "executed_at": ts}, headers=h)
        assert r.status_code == 409

    def test_encrypted_at_rest(self):
        client, h = self._client_token()
        magic_qty, magic_price = 7777, 123455
        ts = datetime(2025, 2, 1, tzinfo=timezone.utc).isoformat()
        client.post("/positions", json={"kind": "buy", "code": "069500", "qty": magic_qty,
                                        "price": magic_price, "executed_at": ts}, headers=h)
        with engine.connect() as conn:  # DB 원시 조회 — 평문 미검출 (§10)
            raw = conn.execute(text(
                "SELECT string_agg(coalesce(qty,'')||coalesce(price,''), '|') FROM trade_transactions"
            )).scalar() or ""
            assert str(magic_qty) not in raw and str(magic_price) not in raw
            raw_lots = conn.execute(text(
                "SELECT string_agg(qty_open||price, '|') FROM position_lots")).scalar() or ""
            assert str(magic_price) not in raw_lots

    def test_deposit_xirr_and_summary(self):
        client, h = self._client_token()
        client.post("/positions", json={"kind": "deposit", "amount": 10_000_000,
                                        "executed_at": datetime(2025, 1, 2, tzinfo=timezone.utc).isoformat()},
                    headers=h)
        s = client.get("/portfolio/summary", headers=h).json()
        assert s["cash"] == 10_000_000 and s["total_equity"] == 10_000_000
        assert s["xirr"] is not None  # 평가 유지 → ≈ 0
        assert abs(s["xirr"]) < 0.05

    def test_owner_isolation(self):
        client, h1 = self._client_token()
        client.cookies.clear()
        _, h2 = self._client_token()
        ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
        client.post("/positions", json={"kind": "buy", "code": "069500", "qty": 10, "price": 70000,
                                        "executed_at": ts}, headers=h1)
        s2 = client.get("/portfolio/summary", headers=h2).json()
        assert s2["positions"] == []  # 타인 포지션 미노출

    def test_from_backtest_conversion_seeds_state(self):
        """전환 시 백테스트 종료 상태(현금·보유)를 이어받는다 (2026-08-28 지시)."""
        from tests.test_backtest_api import run_job_inline, seed_synthetic

        with SessionLocal() as s:
            seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
        client, h = self._client_token()
        bt = client.post("/backtests", json={"capital": 50_000_000, "date_from": "2024-01-02",
                                             "date_to": "2025-08-01"}, headers=h).json()
        # 미완료 잡 전환 거부
        assert client.post(f"/portfolios/from-backtest/{bt['id']}", headers=h).status_code == 409
        run_job_inline(bt["id"])
        r = client.post(f"/portfolios/from-backtest/{bt['id']}", headers=h)
        assert r.status_code == 201
        pf = r.json()
        assert pf["backtest_id"] == bt["id"] and "seeded_cash" in pf
        s2 = client.get(f"/portfolio/summary?portfolio_id={pf['id']}", headers=h).json()
        assert s2["portfolio"]["kind"] == "from_backtest"
        # 총자산(시드 현금+보유 평가) ≈ 백테스트 최종 평가액 (같은 종가 기준 — 반올림 오차 허용)
        got = client.get(f"/backtests/{bt['id']}", headers=h).json()
        final_eq = got["equity"][-1]["equity"]
        assert abs(s2["total_equity"] - final_eq) / final_eq < 0.01
        # 거래 내역 엔드포인트 — 시드 입금 + 이관 매수 확인
        txs = client.get(f"/portfolio/transactions?portfolio_id={pf['id']}", headers=h).json()["items"]
        kinds = {t["kind"] for t in txs}
        assert "deposit" in kinds
        if pf["seeded_lots"] > 0:
            assert "buy" in kinds

    def test_target_stop_meta(self):
        client, h = self._client_token()
        ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
        client.post("/positions", json={"kind": "buy", "code": "069500", "qty": 10, "price": 70000,
                                        "executed_at": ts}, headers=h)
        pid = client.get("/portfolios", headers=h).json()["items"][0]["id"]
        r = client.put(f"/portfolios/{pid}/meta/069500",
                       json={"target_price": 77000, "stop_price": 65000}, headers=h)
        assert r.status_code == 200
        s = client.get("/portfolio/summary", headers=h).json()
        pos = s["positions"][0]
        assert pos["target_price"] == 77000 and pos["stop_price"] == 65000


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
def test_multi_portfolio_create_delete_cascade():
    """다중 실전매매 생성·삭제 (2026-08-28 지시) — 삭제 시 거래·로트 연쇄 제거, 타 포트 영향 없음."""
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
    client = TestClient(app, base_url="https://testserver")
    import uuid as _uuid
    email = f"mp{_uuid.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    a = client.post("/portfolios", json={"name": "전략 A"}, headers=h).json()["id"]
    b = client.post("/portfolios", json={"name": "전략 B"}, headers=h).json()["id"]
    ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
    for pid in (a, b):
        client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                        "qty": 10, "price": 70000, "executed_at": ts}, headers=h)
    # A 삭제 → A만 사라지고 B는 유지
    assert client.delete(f"/portfolios/{a}", headers=h).status_code == 200
    names = [p["name"] for p in client.get("/portfolios", headers=h).json()["items"]]
    assert "전략 A" not in names and "전략 B" in names
    sb = client.get(f"/portfolio/summary?portfolio_id={b}", headers=h).json()
    assert sb["positions"][0]["qty"] == 10  # B 데이터 무결
    # 삭제된 포트 접근 404, 타인 삭제 404
    assert client.get(f"/portfolio/summary?portfolio_id={a}", headers=h).status_code == 404
    client.cookies.clear()
    email2 = f"mp{_uuid.uuid4().hex[:8]}@stocklab.dev"
    t2 = client.post("/auth/register", json={"email": email2, "password": "password123"}).json()["access_token"]
    assert client.delete(f"/portfolios/{b}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
def test_portfolio_equity_curve():
    """실전 수익률 곡선 (2026-08-28 지시): TWR 지수 100 시작, 입금은 지수에 무영향."""
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"eq{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "curve"}, headers=h).json()["id"]
    ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000,
                                    "executed_at": ts}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 100, "price": 70000, "executed_at": ts}, headers=h)
    body = client.get(f"/portfolio/equity?portfolio_id={pid}", headers=h).json()
    items = body["items"]
    assert len(items) > 10
    assert items[0]["index"] == 100.0
    assert all(i["equity"] > 0 for i in items)
    # 중간 입금 → 평가액은 점프하지만 지수는 연속 (입금일 지수 변화 = 시장 수익만)
    mid = items[len(items)//2]["date"]
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 5_000_000,
                                    "executed_at": mid + "T10:00:00+09:00"}, headers=h)
    body2 = client.get(f"/portfolio/equity?portfolio_id={pid}", headers=h).json()
    assert len(body2["items"]) == len(items)
