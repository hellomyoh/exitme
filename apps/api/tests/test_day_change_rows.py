"""계좌별 현황·매매일지 자산의 '오늘 손익' (2026-09-10 지시) — 누적(원가 대비)과 분모가 다른 값을 함께 돌려준다.

오늘 손익 = 현재가(10초 폴링 캐시 우선, 없으면 종가) − **전일 종가**, %는 전일 종가 평가액 대비.
전일 종가가 없는 종목(오늘 신규 매수·시세 미확보)은 빼고 이름을 day_missing 으로 알린다.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]
KST = timezone(timedelta(hours=9))


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"dc{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _seed(code: str, name: str, closes: dict[date, int], type_: str = "ETF") -> None:
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, name, "KOSPI", type_=type_)
        with patch("app.services.ingest.market_session_state", return_value=(max(closes), True)):
            upsert_daily_bars(s, inst.id, [{"trade_date": d, "open": v, "high": v, "low": v, "close": v, "volume": 1}
                                           for d, v in closes.items()], source="kis")
        s.commit()


def test_dashboard_account_row_has_cumulative_and_today(monkeypatch):
    """계좌 행: 누적은 원가 대비, 오늘은 전일 종가 대비. 오늘 산 종목(전일 종가 없음)은 오늘 손익에서 빠지고 이름이 남는다."""
    from app.dashboard import kst_today

    today = kst_today()
    y = today - timedelta(days=1)
    old, new = "D" + uuid.uuid4().hex[:5].upper(), "N" + uuid.uuid4().hex[:5].upper()
    _seed(old, f"보유{old}", {y: 100_000, today: 110_000})     # 전일 100,000 → 오늘 110,000
    _seed(new, f"신규{new}", {today: 50_000})                   # 오늘 상장/첫 종가 — 전일 종가 없음

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "오늘손익", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    d0 = (today - timedelta(days=5)).isoformat() + "T10:00:00+09:00"
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000, "executed_at": d0}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": old, "qty": 10, "price": 90_000, "executed_at": d0}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": new, "qty": 4, "price": 48_000,
                               "executed_at": today.isoformat() + "T10:00:00+09:00"}, headers=h)

    row = next(p for p in c.get("/dashboard", headers=h).json()["portfolios"] if p["id"] == pid)
    # 누적 = (110,000−90,000)×10 + (50,000−48,000)×4 = 208,000, 분모 = 원가 1,092,000
    assert row["pnl"] == 208_000 and abs(row["pnl_pct"] - 208_000 / 1_092_000) < 1e-9
    # 오늘 = (110,000−100,000)×10 = 100,000, 분모 = 전일 종가 평가액 1,000,000 (신규 종목은 제외)
    assert row["day_change"] == 100_000 and abs(row["day_change_pct"] - 0.1) < 1e-9
    assert row["day_missing"] == [f"신규{new}"] and row["price_source"] == "close"


def test_dashboard_account_row_uses_live_quote_when_available(monkeypatch):
    """장중에는 10초 폴링 캐시의 현재가로 평가액·오늘 손익을 계산한다(총자산 카드와 같은 기준)."""
    import app.quotes as q
    from app.dashboard import kst_today

    today = kst_today()
    y = today - timedelta(days=1)
    code = "L" + uuid.uuid4().hex[:5].upper()
    _seed(code, f"실시간{code}", {y: 100_000, today: 110_000})
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "실시간행", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    d0 = (today - timedelta(days=5)).isoformat() + "T10:00:00+09:00"
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 5_000_000, "executed_at": d0}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": code, "qty": 10, "price": 90_000, "executed_at": d0}, headers=h)

    # conftest 가 실시간 캐시를 차단하므로(개발 Redis 의 장중 값이 섞이지 않게) 이 테스트만 값을 되돌려 준다
    now = datetime.now(KST).isoformat(timespec="seconds")
    monkeypatch.setattr(q, "live_quotes", lambda codes: {code: {"price": 115_000, "as_of": now, "change": 0}} if code in list(codes) else {})
    row = next(p for p in c.get("/dashboard", headers=h).json()["portfolios"] if p["id"] == pid)
    assert row["price_source"] == "live"
    assert row["stock_value"] == 10 * 115_000                       # 종가(110,000) 아닌 현재가로 평가
    assert row["day_change"] == 10 * (115_000 - 100_000)            # 전일 종가 대비
    assert abs(row["day_change_pct"] - 0.15) < 1e-9


def test_journal_asset_row_has_today_change(monkeypatch):
    """매매일지 행: 총자산에 넣는 종목만으로 오늘 손익을 계산하고, 전일 종가가 없는 종목은 day_missing 으로 뺀다."""
    import app.mjournal as mj
    from app.dashboard import kst_today

    class _NoKis:
        def fetch_daily(self, code, a, b, org_price=True):
            return []

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: _NoKis())
    mj._PRICE_CACHE.clear()
    mj._CLOSE_MISS.clear()

    today = kst_today()
    y = today - timedelta(days=1)
    a, b = "J" + uuid.uuid4().hex[:5].upper(), "K" + uuid.uuid4().hex[:5].upper()
    _seed(a, f"일지A{a}", {y: 10_000, today: 11_000}, type_="STOCK")
    _seed(b, f"일지B{b}", {today: 20_000}, type_="STOCK")     # 전일 종가 없음

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "오늘손익일지", "symbol": f"일지A{a}", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    for sym, code, qty, price in ((f"일지A{a}", a, 10, 9_000), (f"일지B{b}", b, 5, 19_000)):
        assert c.post(f"/mjournals/{jid}/entries",
                      json={"side": "buy", "qty": qty, "price": price, "trade_date": (today - timedelta(days=3)).isoformat(),
                            "symbol": sym, "code": code}, headers=h).status_code == 201

    row = next(j for j in c.get("/dashboard", headers=h).json()["journals"] if j["id"] == jid)
    # 평가 = 10×11,000 + 5×20,000 = 210,000, 누적 = 210,000 − (90,000+95,000) = 25,000
    assert row["value"] == 210_000 and row["unrealized"] == 25_000
    # 오늘 = 10×(11,000−10,000) = 10,000, 분모 = 100,000. B 는 전일 종가가 없어 제외
    assert row["day_change"] == 10_000 and abs(row["day_change_pct"] - 0.1) < 1e-9
    assert row["day_missing"] == [f"일지B{b}"]


def test_market_card_and_total_card_carry_today_and_cumulative(monkeypatch):
    """상단 카드 (2026-09-10 지시) — 한국 주식 카드는 누적(원가 대비)+오늘(전일 종가 대비),
    총자산 카드는 누적 금액(since_inception_amount, 입출금 제외)을 함께 준다."""
    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import AssetSnapshot, TradePortfolio

    today = kst_today()
    y = today - timedelta(days=1)
    code = "M" + uuid.uuid4().hex[:5].upper()
    _seed(code, f"시장{code}", {y: 100_000, today: 110_000})

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "시장카드", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    with SessionLocal() as s_:
        uid = s_.get(TradePortfolio, pid).user_id
    d0 = (today - timedelta(days=5)).isoformat() + "T10:00:00+09:00"
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 2_000_000, "executed_at": d0}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": code, "qty": 10, "price": 90_000, "executed_at": d0}, headers=h)
    # 어제 스냅샷을 만들어 두면 누적(최초 대비)·오늘(전일 대비)이 모두 성립한다
    with SessionLocal() as s_:
        compute_user_snapshot(s_, uid, y)
        snap = s_.scalar(select(AssetSnapshot).where(AssetSnapshot.user_id == uid, AssetSnapshot.snap_date == y))
        base = int(snap.total)
        s_.commit()

    d = c.get("/dashboard", headers=h).json()
    kr = d["kr_stock"]
    assert kr["pnl"] == 10 * (110_000 - 90_000) and abs(kr["pnl_pct"] - 200_000 / 900_000) < 1e-9   # 누적: 원가 대비
    assert kr["day_change"] == 10 * (110_000 - 100_000) and abs(kr["day_change_pct"] - 0.1) < 1e-9  # 오늘: 전일 종가 대비
    # 누적 금액 = 오늘 총자산 − 최초 스냅샷 총자산 (그 사이 입출금 없음)
    assert d["since_inception_amount"] == d["total"] - base
    assert d["since_inception_pct"] is not None and d["change_amount"] == d["total"] - base

