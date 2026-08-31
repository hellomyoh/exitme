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
        seed_synthetic(s, "102110", "TIGER 200", start=35000.0, seed=13)


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


def test_tiger_etf_selection():
    """ETF 옵션(2026-08-28 지시): TIGER 선택 시 102110 데이터로 실행되고 fingerprint 가 달라진다."""
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    base = {"capital": 100_000_000, "date_from": "2024-01-02", "date_to": "2025-08-01"}
    fps = {}
    for etf in ("KODEX", "TIGER"):
        bt_id = client.post("/backtests", json={**base, "etf": etf}, headers=h).json()["id"]
        assert run_job_inline(bt_id)["status"] == "DONE"
        got = client.get(f"/backtests/{bt_id}", headers=h).json()
        assert got["status"] == "DONE" and got["params"]["etf"] == etf
        fps[etf] = got["data_fingerprint"]
    assert fps["KODEX"] != fps["TIGER"]  # 다른 데이터 세트
    # 잘못된 값 거부
    assert client.post("/backtests", json={**base, "etf": "ARIRANG"}, headers=h).status_code == 422


def test_journal_daily_records():
    """일자별 매매 저널(2026-08-28 지시): 계획·체결·보유·수익률 필드 검증."""
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    bt_id = client.post("/backtests", json={"capital": 100_000_000, "date_from": "2024-01-02",
                                            "date_to": "2025-08-01"}, headers=h).json()["id"]
    run_job_inline(bt_id)
    got = client.get(f"/backtests/{bt_id}/journal", headers=h).json()
    assert got["stale"] is False and len(got["items"]) > 100
    first = got["items"][0]
    assert {"date", "regime", "equity", "day_return", "day_pnl", "total_return",
            "cash", "qty_200", "qty_lev", "planned", "fills"} <= set(first)
    # 체결이 있는 날이 존재하고, 체결 필드 형태 검증
    fill_days = [it for it in got["items"] if it["fills"]]
    assert fill_days, "체결 기록이 있어야 함"
    f = fill_days[0]["fills"][0]
    assert f["side"] in ("buy", "sell") and f["qty"] > 0 and f["price"] > 0
    # 계획(주문표)이 있는 날 존재
    assert any(it["planned"] for it in got["items"])
    # 보유량·현금은 음수 불가
    assert all(it["qty_200"] >= 0 and it["qty_lev"] >= 0 for it in got["items"])
    # 미완료 잡 저널 409 — 동일 조건은 재사용되므로(2026-08-31) 조건을 달리해 QUEUED 잡 생성
    q_id = client.post("/backtests", json={"capital": 100_000_001, "date_from": "2024-01-02",
                                           "date_to": "2025-08-01"}, headers=h).json()["id"]
    assert client.get(f"/backtests/{q_id}/journal", headers=h).status_code == 409


def test_delete_backtest_cascade_and_isolation():
    """기록 삭제(2026-08-28 지시): 자산곡선 연쇄 삭제, 전환 포트 링크 해제, 타인 404."""
    client = TestClient(app, base_url="https://testserver")
    token = make_user(client)
    h = {"Authorization": f"Bearer {token}"}
    bt_id = client.post("/backtests", json={"capital": 50_000_000, "date_from": "2024-01-02",
                                            "date_to": "2025-08-01"}, headers=h).json()["id"]
    run_job_inline(bt_id)
    pf = client.post(f"/portfolios/from-backtest/{bt_id}", headers=h).json()
    # 타인 삭제 404
    client.cookies.clear()
    t2 = make_user(client)
    assert client.delete(f"/backtests/{bt_id}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404
    # 본인 삭제 → 기록 404, 전환 포트는 유지(링크만 해제)
    assert client.delete(f"/backtests/{bt_id}", headers=h).status_code == 200
    assert client.get(f"/backtests/{bt_id}", headers=h).status_code == 404
    s = client.get(f"/portfolio/summary?portfolio_id={pf['id']}", headers=h).json()
    assert s["portfolio"]["backtest_id"] is None
    from app.db import SessionLocal
    from app.models import BacktestEquity
    from sqlalchemy import select as _sel, func as _f
    with SessionLocal() as db:
        left = db.scalar(_sel(_f.count()).where(BacktestEquity.backtest_id == bt_id))
        assert left == 0  # 자산곡선 연쇄 삭제


def test_short_window_backtest_trades():
    """2026-08-28 결함 수정: 1년 미만 구간도 워밍업 선행 로드로 거래 발생 (구간만 로드하면 거래 0)."""
    client = TestClient(app, base_url="https://testserver")
    h = {"Authorization": f"Bearer {make_user(client)}"}
    # 합성 데이터 400봉(2024-01-02~) 후반 2개월 — 선행 350여 봉이 워밍업으로 쓰여야 함
    resp = client.post("/backtests", json={"capital": 100_000_000, "date_from": "2025-05-01",
                                           "date_to": "2025-07-15"}, headers=h)
    assert resp.status_code == 202
    bt_id = resp.json()["id"]
    run_job_inline(bt_id)
    body = client.get(f"/backtests/{bt_id}", headers=h).json()
    assert body["status"] == "DONE"
    assert body["kpi"]["trades"] >= 0
    eq = body["equity"]
    assert eq, "자산곡선이 비어 있으면 안 됨"
    assert eq[0]["date"] >= "2025-05-01", "곡선은 요청 시작일부터"
    # 핵심: 구간 전체가 워밍업에 잠식되지 않고 계획이 생성됨 (레짐이 기록됨)
    assert any(r["regime"] in ("BULL", "NEUTRAL", "BEAR") for r in eq)
    jr = client.get(f"/backtests/{bt_id}/journal", headers=h).json()["items"]
    assert jr and jr[0]["date"] >= "2025-05-01"
    assert any(d["planned"] for d in jr), "짧은 구간에서도 주문 계획이 있어야 함"


def test_identical_backtest_reused():
    """2026-08-31 검토: 동일 조건 + 동일 데이터 지문 재실행 → 새 잡 대신 기존 DONE 재사용."""
    client = TestClient(app, base_url="https://testserver")
    h = {"Authorization": f"Bearer {make_user(client)}"}
    body = {"capital": 30_000_000, "date_from": "2024-06-03", "date_to": "2025-07-01"}
    first = client.post("/backtests", json=body, headers=h).json()
    assert "reused" not in first
    run_job_inline(first["id"])
    second = client.post("/backtests", json=body, headers=h).json()
    assert second.get("reused") is True and second["id"] == first["id"]
    # 조건이 다르면 새 잡
    third = client.post("/backtests", json={**body, "capital": 31_000_000}, headers=h).json()
    assert third["id"] != first["id"]
