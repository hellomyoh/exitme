"""백테스트 잡 수명주기 통합 테스트 — DB 필요 (feature-backtest §12 J1~J4 축소판).

Celery 브로커 없이 태스크 함수를 직접 호출해 검증한다 (같은 코드 경로).
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app
from app.services.ingest import get_or_create_instrument, upsert_daily_bars

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


def seed_synthetic(session, code: str, name: str, n=400, seed=5, start=70000.0):
    inst = get_or_create_instrument(session, code, name, "KOSPI")
    state = seed
    def rnd():
        nonlocal state
        state = (state * 1103515245 + 12345) % (2 ** 31)
        return state / (2 ** 31)
    bars = []
    price = start
    d = date(2024, 1, 2)
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = price
        c = max(price * (1 + (rnd() - 0.5) * 0.03 + 0.0005), 1000)
        h, l = max(o, c) * 1.005, min(o, c) * 0.995
        bars.append({"trade_date": d, "open": round(o), "high": round(h),
                     "low": round(l), "close": round(c), "volume": 1000})
        price = c
        d += timedelta(days=1)
    upsert_daily_bars(session, inst.id, bars, source="pykrx")
    session.commit()
    return inst


@pytest.fixture(scope="module", autouse=True)
def synthetic_market():
    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)


def make_user(client: TestClient) -> str:
    email = f"bt{uuid.uuid4().hex[:10]}@stocklab.dev"
    resp = client.post("/auth/register", json={"email": email, "password": "password123"})
    return resp.json()["access_token"]


def run_job_inline(bt_id: int):
    from app.worker import run_backtest_job

    return run_backtest_job(bt_id)


def test_job_full_lifecycle_and_results():
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    body = {"capital": 100_000_000, "date_from": "2024-01-02", "date_to": "2025-08-01"}
    resp = client.post("/backtests", json=body, headers=h)
    assert resp.status_code == 202
    bt_id = resp.json()["id"]

    out = run_job_inline(bt_id)
    assert out["status"] == "DONE"

    got = client.get(f"/backtests/{bt_id}", headers=h).json()
    assert got["status"] == "DONE" and got["progress"] == 100
    assert got["kpi"]["trades"] >= 0 and isinstance(got["equity"], list) and got["equity"]
    assert got["data_fingerprint"] and got["stale"] is False
    # 자산곡선 레코드에 레짐·노출 포함
    assert {"date", "equity", "benchmark", "regime", "exposure"} <= set(got["equity"][0])


def test_owner_isolation_404():
    client = TestClient(app, base_url="https://testserver")
    t1 = make_user(client)
    client.cookies.clear()
    t2 = make_user(client)
    resp = client.post("/backtests", json={"capital": 50_000_000, "date_from": "2024-01-02",
                                           "date_to": "2025-01-02"}, headers={"Authorization": f"Bearer {t1}"})
    bt_id = resp.json()["id"]
    assert client.get(f"/backtests/{bt_id}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404


def test_cancel_before_run_results_in_canceled():
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    resp = client.post("/backtests", json={"capital": 50_000_000, "date_from": "2024-01-02",
                                           "date_to": "2025-08-01"}, headers=h)
    bt_id = resp.json()["id"]
    assert client.post(f"/backtests/{bt_id}/cancel", headers=h).status_code == 200
    out = run_job_inline(bt_id)
    assert out["status"] == "CANCELED"
    got = client.get(f"/backtests/{bt_id}", headers=h).json()
    assert got["status"] == "CANCELED"
    # 부분 결과 미저장
    assert "equity" not in got


def test_rerun_same_params_reproducible():
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    body = {"capital": 100_000_000, "date_from": "2024-01-02", "date_to": "2025-08-01"}
    ids = []
    for _ in range(2):
        bt_id = client.post("/backtests", json=body, headers=h).json()["id"]
        run_job_inline(bt_id)
        ids.append(bt_id)
    a = client.get(f"/backtests/{ids[0]}", headers=h).json()
    b = client.get(f"/backtests/{ids[1]}", headers=h).json()
    assert a["kpi"] == b["kpi"]
    assert a["equity"] == b["equity"]
    assert a["data_fingerprint"] == b["data_fingerprint"]


def test_invalid_period_422():
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    resp = client.post("/backtests", json={"capital": 50_000_000, "date_from": "2025-01-02",
                                           "date_to": "2024-01-02"}, headers=h)
    assert resp.status_code == 422
