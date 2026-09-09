"""장중 현재가 시계열 (2026-09-09 실시간 그래프) — 누적·정렬·길이 상한, 조회 API, 빈 시계열의 1분봉 1회 백필(10분 잠금). DB·Redis 필요."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

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
KST = timezone(timedelta(hours=9))


class _R:
    """Redis 흉내 — 리스트·문자열 최소 구현."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}

    def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)

    def ltrim(self, k, a, b):
        lst = self.lists.get(k, [])
        self.lists[k] = lst[a:] if b == -1 else lst[a:b + 1]

    def expire(self, k, ttl):
        return True

    def lrange(self, k, a, b):
        return list(self.lists.get(k, []))

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        return True


def test_series_append_read_and_cap():
    import app.quotes as q

    r = _R()
    day = date(2026, 9, 9)
    q.append_sample(r, "102110", day, 1000, 110_660)
    q.append_sample(r, "102110", day, 1010, 110_700)
    q.append_sample(r, "102110", day, 1005, 110_680)   # 늦게 도착한 점도 시각순으로
    assert q.read_series(r, "102110", day) == [{"t": 1000, "p": 110_660}, {"t": 1005, "p": 110_680}, {"t": 1010, "p": 110_700}]
    assert q.read_series(r, "069500", day) == []
    monkey_max = q.SERIES_MAX
    q.SERIES_MAX = 3
    try:
        for i in range(5):
            q.append_sample(r, "X", day, 2000 + i, 1)
        assert [p["t"] for p in q.read_series(r, "X", day)] == [2002, 2003, 2004]
    finally:
        q.SERIES_MAX = monkey_max


def test_backfill_once_per_ten_minutes():
    import app.quotes as q
    from app.services.kis_client import MinuteBar

    class _Kis:
        calls = 0

        def fetch_minutes_day(self, code, day):
            _Kis.calls += 1
            base = datetime(2026, 9, 9, 9, 0, tzinfo=KST)
            return [MinuteBar(ts=base + timedelta(minutes=i), open=1, high=1, low=1, close=110_000 + i, volume=1) for i in range(3)]

    r = _R()
    day = date(2026, 9, 9)
    assert q.backfill_from_minutes(r, "102110", day, _Kis()) == 3
    assert [p["p"] for p in q.read_series(r, "102110", day)] == [110_000, 110_001, 110_002]
    assert q.backfill_from_minutes(r, "102110", day, _Kis()) == 0 and _Kis.calls == 1   # 잠금 — 다시 부르지 않는다


def test_series_endpoint_requires_auth_and_returns_today():
    import redis as sync_redis

    import app.quotes as q
    from app.config import get_settings
    from app.dashboard import kst_today

    c = TestClient(app, base_url="https://testserver")
    assert c.get("/quotes/series?code=102110").status_code in (401, 403)
    tok = c.post("/auth/register", json={"email": f"qs{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    code = f"T{uuid.uuid4().hex[:6].upper()}"   # 고유 코드 — 공유 Redis 오염 방지, 백필 대상(KIS) 아님이라 빈 결과
    r = sync_redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        q.append_sample(r, code, kst_today(), 1_700_000_000, 12345)
        j = c.get(f"/quotes/series?code={code}", headers=h).json()
        assert j["code"] == code and j["date"] == kst_today().isoformat() and j["items"] == [{"t": 1_700_000_000, "p": 12345}] and j["backfilled"] == 0
    finally:
        r.delete(q.series_key(code, kst_today()))
