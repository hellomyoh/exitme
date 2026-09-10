"""자산 추이의 매매일지 계열 (2026-09-10 지시).

일지 단위 스냅샷은 없으므로 사용자 합계(AssetSnapshot.journal)를 한 줄로 돌려준다.
집계가 없던 날(journal IS NULL, 0020 이전)은 0 으로 그리면 없던 급락이 생기므로 점을 찍지 않는다.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

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

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


def _client():
    c = TestClient(app, base_url="https://testserver")
    email = f"tj{uuid.uuid4().hex[:8]}@x.dev"
    tok = c.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    with SessionLocal() as s:
        from sqlalchemy import select

        from app.models import User

        uid = s.scalar(select(User.id).where(User.email == email))
    return c, h, uid


def _snaps(uid: int, rows: list[tuple]) -> None:
    """(날짜, 총자산, 매매일지) 스냅샷을 직접 적재 — 배치 없이 추이 응답만 본다."""
    from app.models import AssetSnapshot

    with SessionLocal() as s:
        for d, total, journal in rows:
            s.add(AssetSnapshot(user_id=uid, snap_date=d, total=total, stock=0,
                                cash=total - (journal or 0), other=0, journal=journal))
        s.commit()


def test_trend_returns_journal_series_and_skips_days_without_aggregate():
    from app.dashboard import kst_today

    today = kst_today()
    c, h, uid = _client()
    _snaps(uid, [
        (today - timedelta(days=3), 10_000_000, None),        # 집계 이전 — 점을 찍지 않는다
        (today - timedelta(days=2), 11_000_000, 1_000_000),
        (today - timedelta(days=1), 12_000_000, 1_200_000),
        (today, 12_500_000, 1_300_000),
    ])

    body = c.get("/portfolio/trend?range_=3M", headers=h).json()
    assert len(body["items"]) == 4                              # 총자산 선은 그대로 4일
    j = [sr for sr in body["series"] if sr.get("kind") == "journal"]
    assert len(j) == 1
    assert j[0]["name"] == "매매일지" and j[0]["currency"] == "KRW"
    assert [p["equity"] for p in j[0]["points"]] == [1_000_000, 1_200_000, 1_300_000]
    assert (today - timedelta(days=3)).isoformat() not in [p["date"] for p in j[0]["points"]]


def test_trend_omits_journal_series_when_user_has_no_journal_assets():
    """일지가 없어 매일 0 인 사용자에게는 0 만 그리는 빈 줄을 만들지 않는다."""
    from app.dashboard import kst_today

    today = kst_today()
    c, h, uid = _client()
    _snaps(uid, [(today - timedelta(days=i), 5_000_000, 0) for i in (2, 1, 0)])

    body = c.get("/portfolio/trend?range_=3M", headers=h).json()
    assert [sr for sr in body["series"] if sr.get("kind") == "journal"] == []


def test_trend_portfolio_series_are_tagged_as_portfolio():
    """웹이 계열 종류로 색·선 모양을 나누므로 실전매매 줄에도 kind 가 붙는다."""
    from app.dashboard import compute_user_snapshot, kst_today

    today = kst_today()
    c, h, uid = _client()
    pid = c.post("/portfolios", json={"name": "추이계열", "market": "KR", "code_200": "102110"},
                 headers=h).json()["id"]
    d0 = (today - timedelta(days=4)).isoformat() + "T10:00:00+09:00"
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 3_000_000,
                               "executed_at": d0}, headers=h)
    with SessionLocal() as s:
        for i in (2, 1):
            compute_user_snapshot(s, uid, today - timedelta(days=i))
        s.commit()

    body = c.get("/portfolio/trend?range_=3M", headers=h).json()
    mine = [sr for sr in body["series"] if sr.get("portfolio_id") == pid]
    assert len(mine) == 1 and mine[0]["kind"] == "portfolio"
