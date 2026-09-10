"""실시간 시세 반영 (2026-09-10 지시 "종목 수익률·총자본·금액 모두 10초 실시간") — 10초 폴링이 Redis 에 남긴 현재가를
실전매매 요약·매매일지 평가·대시보드 총자산이 먼저 쓰고, 없으면 종가로 폴백한다. KIS 호출은 늘리지 않는다. DB·Redis 필요."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app
from app.quotes import live_quotes as _real_live_quotes   # conftest 의 차단 픽스처보다 먼저 잡아 둔 진짜 구현

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).isoformat(timespec="seconds")


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"lp{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _seed(code: str, closes: dict[date, int]) -> None:
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, f"실시간{code}", "KOSPI", type_="ETF")
        # 오늘 봉은 확정봉 가드(15:30 전 거부)에 걸리므로 심는 순간만 장 마감 상태로
        with patch("app.services.ingest.market_session_state", return_value=(max(closes), True)):
            upsert_daily_bars(s, inst.id, [{"trade_date": d, "open": v, "high": v, "low": v, "close": v, "volume": 1}
                                           for d, v in closes.items()], source="kis")
        s.commit()


def test_live_quotes_reads_cache_and_skips_missing_or_broken():
    """`quotes:last:{code}` 의 폴링 값만 읽는다 — 없는 코드·깨진 JSON·가격 0 은 건너뛰고, Redis 장애면 빈 dict."""
    import redis as sync_redis

    from app.config import get_settings
    from app.quotes import cache_key

    r = sync_redis.from_url(get_settings().redis_url, decode_responses=True)
    ok, zero, broken, absent = (f"L{uuid.uuid4().hex[:5].upper()}" for _ in range(4))
    try:
        r.set(cache_key(ok), json.dumps({"code": ok, "price": 112400, "change": 1740, "as_of": NOW}), ex=60)
        r.set(cache_key(zero), json.dumps({"code": zero, "price": 0, "as_of": NOW}), ex=60)
        r.set(cache_key(broken), "not-json", ex=60)
        out = _real_live_quotes([ok, zero, broken, absent, ok])   # 중복 코드도 한 번만
        assert set(out) == {ok} and out[ok]["price"] == 112400 and out[ok]["as_of"] == NOW and out[ok]["change"] == 1740
        assert _real_live_quotes([]) == {}
    finally:
        r.delete(cache_key(ok), cache_key(zero), cache_key(broken))


def test_portfolio_summary_prefers_live_price_over_close(monkeypatch):
    """종목 카드·평가액·수익률·하루 변동이 실시간 현재가로 계산된다. 캐시가 없으면 종가(delayed) — 같은 화면이 폴백한다."""
    import app.quotes as q
    from app.dashboard import kst_today

    today = kst_today()
    code = "P" + uuid.uuid4().hex[:5].upper()
    _seed(code, {today - timedelta(days=1): 110_660})     # 어제 종가만 있고 오늘 봉은 아직 없다 (장중 상태)
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "실시간", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000,
                               "executed_at": (today - timedelta(days=5)).isoformat() + "T10:00:00+09:00"}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": code, "qty": 17, "price": 112_340,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T10:00:00+09:00"}, headers=h)
    # ① 캐시 없음 → 종가 기준 (종전 동작 그대로)
    s0 = c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()
    p0 = s0["positions"][0]
    assert p0["price"] == 110_660 and p0["price_source"] == "종가" and s0["delayed"] is True and s0["live_at"] is None
    # 실시간이 없으면 평가가격 = 전일 종가라 '오늘 변동' 은 0 (오늘 종가가 적재되면 그때 실제 등락이 잡힌다)
    assert p0["prev_close"] == 110_660 and p0["day_change"] == 0
    # ② 캐시 있음 → 실시간 기준
    monkeypatch.setattr(q, "live_quotes", lambda codes: {code: {"price": 112_400, "as_of": NOW, "change": 1_740}} if code in list(codes) else {})
    s1 = c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()
    p1 = s1["positions"][0]
    assert p1["price"] == 112_400 and p1["price_source"] == "실시간" and p1["value"] == 17 * 112_400
    assert p1["unrealized"] == 17 * (112_400 - 112_340) == 1_020
    assert p1["prev_close"] == 110_660 and p1["day_change"] == 17 * (112_400 - 110_660)
    assert s1["delayed"] is False and s1["live_at"] == NOW and s1["live_count"] == 1
    assert s1["stock_value"] == 17 * 112_400 and s1["total_equity"] == s1["stock_value"] + s1["cash"]


def test_dashboard_live_overlays_display_only_and_keeps_snapshot_on_closes(monkeypatch):
    """`/dashboard/live` 는 방문 기록·스냅샷 적재 없이 총액만 다시 평가한다. 저장된 스냅샷은 종가 기준으로 남는다 (추이·전일 대비 기준 불변)."""
    import app.quotes as q
    from app.dashboard import kst_today
    from app.models import AssetSnapshot, PortfolioSnapshot, TradePortfolio
    from sqlalchemy import select

    today = kst_today()
    code = "D" + uuid.uuid4().hex[:5].upper()
    _seed(code, {today - timedelta(days=1): 110_660})
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "대시", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    with SessionLocal() as s:
        uid = s.get(TradePortfolio, pid).user_id
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000,
                               "executed_at": (today - timedelta(days=5)).isoformat() + "T10:00:00+09:00"}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": code, "qty": 17, "price": 112_340,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T10:00:00+09:00"}, headers=h)
    d0 = c.get("/dashboard", headers=h).json()
    assert d0["live_at"] is None and d0["stock"] == 17 * 110_660          # 캐시 없음 → 종가
    assert c.get("/dashboard/live", headers=h).json() == {"live_at": None}
    monkeypatch.setattr(q, "live_quotes", lambda codes: {code: {"price": 112_400, "as_of": NOW, "change": 0}} if code in list(codes) else {})
    live = c.get("/dashboard/live", headers=h).json()
    assert live["live_at"] == NOW and live["stock"] == 17 * 112_400
    assert live["total"] == live["stock"] + live["cash"] + live["other"] + live["journal"]
    d1 = c.get("/dashboard", headers=h).json()
    assert d1["stock"] == 17 * 112_400 and d1["total"] == live["total"]   # 화면 숫자는 실시간
    with SessionLocal() as s:                                             # 적재된 스냅샷은 종가 그대로
        a = s.scalar(select(AssetSnapshot).where(AssetSnapshot.user_id == uid, AssetSnapshot.snap_date == today))
        ps = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pid, PortfolioSnapshot.snap_date == today))
        assert int(a.stock) == 17 * 110_660 and int(ps.stock_value) == 17 * 110_660


def test_journal_valuation_prefers_live_price(monkeypatch):
    """매매일지 보유 평가도 실시간 우선 — price_source '실시간', summary.live_at 기록. 캐시가 없으면 종전 경로(종가)."""
    import app.mjournal as mj
    import app.quotes as q
    from app.dashboard import kst_today

    today = kst_today()
    code = "J" + uuid.uuid4().hex[:5].upper()
    _seed(code, {today - timedelta(days=1): 10_000})
    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: type("_N", (), {"fetch_daily": lambda *a, **k: []})())
    mj._PRICE_CACHE.clear(); mj._CLOSE_MISS.clear()
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "실시간일지", "symbol": f"실시간{code}", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    assert c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 10, "price": 9_000,
                                                    "trade_date": (today - timedelta(days=3)).isoformat(), "code": code}, headers=h).status_code == 201
    j0 = c.get(f"/mjournals/{jid}", headers=h).json()
    assert j0["holdings"][0]["price_source"] == "종가" and j0["summary"]["live_at"] is None and j0["summary"]["eval_total"] == 100_000
    monkeypatch.setattr(q, "live_quotes", lambda codes: {code: {"price": 11_000, "as_of": NOW, "change": 0}} if code in list(codes) else {})
    j1 = c.get(f"/mjournals/{jid}", headers=h).json()
    hd = j1["holdings"][0]
    assert hd["price"] == 11_000 and hd["price_source"] == "실시간" and hd["eval"] == 110_000
    assert j1["summary"]["live_at"] == NOW and j1["summary"]["eval_total"] == 110_000 and j1["summary"]["unrealized_total"] == 20_000
