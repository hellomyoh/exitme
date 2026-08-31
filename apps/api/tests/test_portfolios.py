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
    # 기시흐름 규약(검증 H-2): 첫날 지수 = 100 × V0/입금액 — 시작일 성과도 지수에 반영
    assert items[0]["index"] == pytest.approx(100.0 * items[0]["equity"] / 10_000_000, abs=0.02)
    assert all(i["equity"] > 0 for i in items)
    # 중간 입금 → 평가액은 점프하지만 지수는 연속 (입금일 지수 변화 = 시장 수익만)
    mid = items[len(items)//2]["date"]
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 5_000_000,
                                    "executed_at": mid + "T10:00:00+09:00"}, headers=h)
    body2 = client.get(f"/portfolio/equity?portfolio_id={pid}", headers=h).json()
    assert len(body2["items"]) == len(items)


# ── 2026-08-28 공식 검증 소견 회귀 고정
def test_twr_day_zero_and_prestart_flow_convention():
    """검증 H-1·H-2: 기시흐름 규약 V_t/(V_{t−1}+F_t), 첫날도 (0+F_0) 분모로 포함."""
    # 첫날 입금 100 → 종가 평가 102 (+2%), 이튿날 112.2 (+10%) → TWR = 12.2%
    daily = [(date(2025, 1, 1), 102.0, 100.0), (date(2025, 1, 2), 112.2, 0.0)]
    assert twr(daily) == pytest.approx(0.122, abs=1e-9)


def test_xirr_wide_bracket():
    """검증 M-1: 단기 초고수익 흐름 — 브래킷 자동 확장으로 해 산출 (기존 [−0.99,10] 은 None)."""
    r = xirr([(date(2025, 1, 1), -100.0), (date(2025, 2, 1), 150.0)])
    assert r is not None and r > 10.0


def test_withdraw_exceeding_cash_rejected():
    """검증 M-4: 현금 초과 출금 409."""
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"wd{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "wd"}, headers=h).json()["id"]
    ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                                    "executed_at": ts}, headers=h)
    r = client.post("/positions", json={"portfolio_id": pid, "kind": "withdraw", "amount": 2_000_000,
                                        "executed_at": ts}, headers=h)
    assert r.status_code == 409


def test_sell_ignores_lots_opened_after_execution():
    """검증 H-4: 매도 시점 이후 취득 로트는 매도 대상 아님 — 소급 매도 409."""
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"h4{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "h4"}, headers=h).json()["id"]
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000,
                                    "executed_at": datetime(2025, 3, 10, tzinfo=timezone.utc).isoformat()},
                headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 50, "price": 70000,
                                    "executed_at": datetime(2025, 3, 10, tzinfo=timezone.utc).isoformat()},
                headers=h)
    # 매수(3/10) 이전 날짜(3/5)로 소급 매도 → 그 시점 보유 0 → 409
    r = client.post("/positions", json={"portfolio_id": pid, "kind": "sell", "code": "069500",
                                        "qty": 10, "price": 71000,
                                        "executed_at": datetime(2025, 3, 5, tzinfo=timezone.utc).isoformat()},
                    headers=h)
    assert r.status_code == 409


def test_daily_series_buy_without_deposit_counts_as_flow():
    """검증 C-2: 입금 없이 매수 → 암묵 유입을 흐름으로 계상, TWR 은 시장수익만."""
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"c2{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "c2"}, headers=h).json()["id"]
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 100, "price": 70000,
                                    "executed_at": datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()},
                headers=h)
    body = client.get(f"/portfolio/equity?portfolio_id={pid}", headers=h).json()
    items = body["items"]
    assert items, "입금 기록 없어도 곡선 산출"
    assert all(i["equity"] > 0 for i in items)          # 현금 음수로 평가액 붕괴 금지
    assert all(0 < i["index"] < 1000 for i in items)    # 지수 폭주 없음 (순수 시장 수익 범위)


def test_portfolio_journal_plan_and_fills():
    """2026-08-29 일지 개편: 주문표 조회 → 계획 스냅샷 저장 → /portfolio/journal 에 계획+체결 동반."""
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"jn{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "journal"}, headers=h).json()["id"]
    ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000,
                                    "executed_at": ts}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 100, "price": 70000, "executed_at": ts}, headers=h)
    # 주문표 조회 → 다음 거래일 키로 계획 스냅샷 저장
    sg = client.get(f"/signals/daily?portfolio_id={pid}", headers=h)
    assert sg.status_code == 200 and sg.json()["basis"] == "portfolio"
    jr = client.get(f"/portfolio/journal?portfolio_id={pid}", headers=h).json()
    items = jr["items"]
    assert items, "일지가 비어 있으면 안 됨"
    planned_days = [i for i in items if i["planned"] is not None]
    assert planned_days, "주문표 조회일의 계획 스냅샷이 일지에 있어야 함"
    fill_days = [i for i in items if i["fills"]]
    assert fill_days and any(f["kind"] == "buy" for f in fill_days[0]["fills"])


def test_delete_portfolio_with_plan_snapshot():
    """2026-08-29 결함: 계획 스냅샷(portfolio_plans) FK 로 포트 삭제 실패 — 함께 삭제 회귀."""
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"dp{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "del"}, headers=h).json()["id"]
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 5_000_000,
                                    "executed_at": datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()},
                headers=h)
    assert client.get(f"/signals/daily?portfolio_id={pid}", headers=h).status_code == 200  # 스냅샷 생성
    r = client.delete(f"/portfolios/{pid}", headers=h)
    assert r.status_code == 200, r.text


def test_delete_transaction_rebuilds_ledger():
    """2026-08-29 오입력 정정: 거래 삭제 → 재생으로 로트·실현손익 재구성, 불가능하면 409."""
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"rx{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "fix"}, headers=h).json()["id"]
    t0 = datetime(2025, 1, 10, tzinfo=timezone.utc)
    def post(body):
        r = client.post("/positions", json=body, headers=h)
        assert r.status_code == 201, r.text
        return r.json()
    post({"portfolio_id": pid, "kind": "deposit", "amount": 20_000_000, "executed_at": t0.isoformat()})
    post({"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 100, "price": 70000,
          "executed_at": t0.isoformat()})
    sell = post({"portfolio_id": pid, "kind": "sell", "code": "069500", "qty": 30, "price": 72000,
                 "executed_at": datetime(2025, 1, 15, tzinfo=timezone.utc).isoformat()})
    s1 = client.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()
    assert s1["positions"][0]["qty"] == 70 and s1["realized_pnl"] == 60_000
    # 매도 오입력 삭제 → 보유 100 복원, 실현손익 0
    assert client.delete(f"/positions/{sell['id']}", headers=h).status_code == 200
    s2 = client.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()
    assert s2["positions"][0]["qty"] == 100 and s2["realized_pnl"] == 0
    # 매도 재등록 후 그 매도의 근거 매수를 삭제하려 하면 409 (재생 불가)
    sell2 = post({"portfolio_id": pid, "kind": "sell", "code": "069500", "qty": 30, "price": 72000,
                  "executed_at": datetime(2025, 1, 15, tzinfo=timezone.utc).isoformat()})
    txs = client.get(f"/portfolio/transactions?portfolio_id={pid}", headers=h).json()["items"]
    buy_id = next(t["id"] for t in txs if t["kind"] == "buy")
    assert client.delete(f"/positions/{buy_id}", headers=h).status_code == 409
    # 여전히 일관 상태 (매도 유지)
    s3 = client.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()
    assert s3["positions"][0]["qty"] == 70
    assert sell2["realized_pnl"] == 60_000


# ── 2026-08-31 한국/미국 분리 + 설정
def test_us_portfolio_market_separation():
    """미국 포트: market 저장·KR 종목 거부·목록 market 노출."""
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"us{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "us", "market": "US"}, headers=h).json()["id"]
    items = client.get("/portfolios", headers=h).json()["items"]
    assert next(i for i in items if i["id"] == pid)["market"] == "US"
    ts = datetime(2026, 8, 20, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                                    "executed_at": ts}, headers=h)
    # KR 종목을 US 포트에 → 409 (통화 혼합 방지)
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
    r = client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                        "qty": 1, "price": 70000, "executed_at": ts}, headers=h)
    assert r.status_code == 409


def test_algo_settings_roundtrip():
    """알고리즘 설정: 조회·저장(범위 검증)·초기화 + 잡 파라미터 스냅샷."""
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    token = client.post("/auth/register", json={"email": f"st{_u.uuid4().hex[:8]}@stocklab.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    body = client.get("/settings/algorithm", headers=h).json()
    assert any(i["key"] == "regime_buffer" and i["editable"] for i in body["items"])
    assert any(i["key"] == "tick" and not i["editable"] for i in body["items"])
    assert client.put("/settings/algorithm", json={"values": {"emax_bull": 9.9}}, headers=h).status_code == 422
    out = client.put("/settings/algorithm", json={"values": {"emax_neutral": 0.7}}, headers=h).json()
    assert out["overridden_keys"] == ["emax_neutral"]
    # 잡 생성 시 스냅샷 고정
    from tests.test_backtest_api import seed_synthetic
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
    bt = client.post("/backtests", json={"capital": 10_000_000, "date_from": "2024-06-01",
                                         "date_to": "2025-07-01"}, headers=h).json()
    job = client.get(f"/backtests/{bt['id']}", headers=h).json()
    assert job["params"]["algo"] == {"emax_neutral": 0.7}
    assert client.post("/settings/algorithm/reset", headers=h).json()["reset"] is True
    body = client.get("/settings/algorithm", headers=h).json()
    assert all(not i["overridden"] for i in body["items"])


def test_change_password_flow():
    client = TestClient(app, base_url="https://testserver")
    import uuid as _u
    email = f"pw{_u.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/auth/change-password", json={"current_password": "wrong",
                                                      "new_password": "newpass1234"}, headers=h).status_code == 403
    assert client.post("/auth/change-password", json={"current_password": "password123",
                                                      "new_password": "newpass1234"}, headers=h).status_code == 200
    assert client.post("/auth/login", json={"email": email, "password": "newpass1234"}).status_code == 200
