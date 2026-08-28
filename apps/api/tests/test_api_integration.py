"""DB 통합 테스트 — compose 환경(db 서비스)에서 실행. DB 미접속 시 skip.

검증: /health, 적재 멱등(ON CONFLICT), 검증 거부 행 미적재, /ohlcv 수정주가·as_of.
"""
from datetime import date

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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB_UP, reason="database not reachable"),
]


@pytest.fixture()
def session():
    with SessionLocal() as s:
        yield s
        s.rollback()


def bar(d: date, o: int, h: int, l: int, c: int, v: int) -> dict:
    return {"trade_date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_upsert_idempotent_and_validated(session):
    inst = get_or_create_instrument(session, "TEST01", "테스트 ETF", "KOSPI")
    bars = [
        bar(date(2026, 8, 26), 100, 110, 95, 105, 1000),
        bar(date(2026, 8, 27), 0, 110, 95, 105, 1000),  # 검증 거부 대상 (시가 0)
    ]
    res1 = upsert_daily_bars(session, inst.id, bars, source="pykrx")
    assert res1.inserted == 1 and res1.rejected == 1
    res2 = upsert_daily_bars(session, inst.id, bars, source="pykrx")
    assert res2.inserted == 0 and res2.skipped_conflict == 1  # 멱등


def test_ohlcv_endpoint_adjusted_price(session):
    inst = get_or_create_instrument(session, "TEST02", "테스트 ETF 2", "KOSPI")
    upsert_daily_bars(session, inst.id, [bar(date(2026, 8, 25), 100, 110, 95, 105, 1000)], source="pykrx")
    # adj_factor 0.5 시나리오 (액면분할) — 수정주가 = raw × factor
    from app.models import OhlcvDaily
    row = session.get(OhlcvDaily, (inst.id, date(2026, 8, 25)))
    row.adj_factor = 0.5
    session.commit()

    client = TestClient(app)
    resp = client.get("/ohlcv", params={"code": "TEST02", "from": "2026-08-01", "to": "2026-08-31"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["delayed"] is True and body["as_of"] is not None
    assert body["items"][0]["close"] == 52  # round(105 × 0.5) = 52
    # 정리
    session.delete(row)
    session.commit()


def test_ohlcv_unknown_code_problem_json():
    client = TestClient(app)
    resp = client.get("/ohlcv", params={"code": "NOPE", "from": "2026-08-01", "to": "2026-08-31"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
