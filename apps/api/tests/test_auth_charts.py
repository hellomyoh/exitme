"""인증·차트 저장 API 통합 테스트 — 소유자 격리 검증 (ADR-003, feature-chart §12)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


def register(client: TestClient, email: str) -> str:
    resp = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert resp.status_code == 201
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def unique_email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@stocklab.dev"


def test_register_login_refresh_flow():
    client = TestClient(app, base_url="https://testserver")
    email = unique_email()
    register(client, email)
    # 중복 가입 거부
    assert client.post("/auth/register", json={"email": email, "password": "password123"}).status_code == 409
    # 로그인
    resp = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert resp.status_code == 200
    assert "refresh_token" in resp.cookies
    # 잘못된 비밀번호
    assert client.post("/auth/login", json={"email": email, "password": "wrongpass1"}).status_code == 401
    # refresh 회전
    resp2 = client.post("/auth/refresh")
    assert resp2.status_code == 200 and resp2.json()["access_token"]


def test_layout_owner_isolation():
    client = TestClient(app, base_url="https://testserver")
    t1 = register(client, unique_email())
    client.cookies.clear()
    t2 = register(client, unique_email())

    r = client.put("/chart/layouts", json={"name": "main", "config": {"ma": [20, 60]}}, headers=auth(t1))
    assert r.status_code == 200
    # 본인 조회 OK
    mine = client.get("/chart/layouts", headers=auth(t1)).json()["items"]
    assert any(i["name"] == "main" for i in mine)
    # 타인에게는 보이지 않음
    others = client.get("/chart/layouts", headers=auth(t2)).json()["items"]
    assert not any(i["name"] == "main" for i in others)
    # 무토큰 401
    assert client.get("/chart/layouts").status_code == 401


def test_drawings_roundtrip():
    from app.db import SessionLocal
    from app.services.ingest import get_or_create_instrument

    with SessionLocal() as s:
        get_or_create_instrument(s, "TEST01", "테스트 ETF", "KOSPI")
        s.commit()
    client = TestClient(app, base_url="https://testserver")
    t = register(client, unique_email())
    items = {"hlines": [{"price": 70000}], "trend": [{"a": [1, 2], "b": [3, 4]}]}
    r = client.put("/chart/drawings", params={"code": "TEST01"}, json={"items": items}, headers=auth(t))
    assert r.status_code == 200
    back = client.get("/chart/drawings", params={"code": "TEST01"}, headers=auth(t)).json()
    assert back["items"] == items  # JSON 직렬화 왕복 무손실
