"""'오늘 손익'의 기준일 (2026-09-12 지시) — 새 봉이 없는 날은 마지막 거래일의 변동을 보여준다.

종전에는 현재가·기준가가 모두 "오늘 이전 마지막 종가"라 새 봉이 없으면 항상 0 이었다. 해당되는 날이 넷이다:
주말·공휴일·장 시작 전(오늘 봉 아직 없음)·일봉 적재 지연. 규칙은 하나 —
**기준은 지금 쓰는 가격 바로 직전 값**. 장중이면 실시간 vs 마지막 종가, 아니면 마지막 종가 vs 그 직전 종가.
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
    tok = c.post("/auth/register", json={"email": f"as{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _seed(code: str, name: str, closes: dict[date, int], type_: str = "ETF") -> None:
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, name, "KOSPI", type_=type_)
        with patch("app.services.ingest.market_session_state", return_value=(max(closes), True)):
            upsert_daily_bars(s, inst.id, [{"trade_date": d, "open": v, "high": v, "low": v, "close": v, "volume": 1}
                                           for d, v in closes.items()], source="kis")
        s.commit()


def _port_with_holding(code: str, qty: int, price: int, bought: date):
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "기준일", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    at = bought.isoformat() + "T10:00:00+09:00"
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 50_000_000, "executed_at": at}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": code, "qty": qty, "price": price,
                               "executed_at": at}, headers=h)
    return c, h, pid


# ── 단위: 두 기준점 고르기 ───────────────────────────────────────────────────────

def test_day_change_basis_uses_the_last_two_closes_when_no_live_quote():
    """실시간 시세가 없으면 현재가 = 마지막 종가, 기준 = 그 직전 종가 — 그 마지막 봉 날짜가 기준일."""
    from app.dashboard import day_change_basis
    from app.models import Instrument

    code = "AS" + uuid.uuid4().hex[:4].upper()
    d1, d2 = date(2026, 9, 9), date(2026, 9, 10)
    _seed(code, f"기준{code}", {date(2026, 9, 7): 100_000, d1: 112_400, d2: 112_150})
    with SessionLocal() as s:
        iid = s.scalar(select(Instrument.id).where(Instrument.code == code))
        now_px, prev_px, asof = day_change_basis(s, {iid}, {})
    assert (now_px[iid], prev_px[iid]) == (112_150, 112_400)     # 09-10 종가 vs 09-09 종가
    assert asof == d2                                            # 그 변동이 일어난 날


def test_day_change_basis_keeps_live_vs_last_close_during_the_session():
    """장중(실시간 시세 있음)은 종전 그대로 — 현재가 = 실시간, 기준 = 마지막 종가, 기준일 None(=오늘)."""
    from app.dashboard import day_change_basis
    from app.models import Instrument

    code = "AL" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"장중{code}", {date(2026, 9, 9): 112_400, date(2026, 9, 10): 112_150})
    with SessionLocal() as s:
        iid = s.scalar(select(Instrument.id).where(Instrument.code == code))
        now_px, prev_px, asof = day_change_basis(s, {iid}, {iid: 115_000.0})
    assert (now_px[iid], prev_px[iid]) == (115_000.0, 112_150)   # 실시간 vs 마지막 종가
    assert asof is None


def test_day_change_basis_skips_instruments_with_a_single_bar():
    """봉이 하나뿐이면(신규 상장) 비교 기준이 없어 오늘 손익에서 빠진다 — 0 으로 세지 않는다."""
    from app.dashboard import day_change_basis
    from app.models import Instrument

    code = "A1" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"신규{code}", {date(2026, 9, 10): 50_000})
    with SessionLocal() as s:
        iid = s.scalar(select(Instrument.id).where(Instrument.code == code))
        now_px, prev_px, asof = day_change_basis(s, {iid}, {})
    assert now_px[iid] == 50_000 and iid not in prev_px and asof is None


# ── 통합: 주말·휴장·장 시작 전·적재 지연 = 같은 상황 ────────────────────────────────

def test_dashboard_shows_the_last_trading_day_move_when_today_has_no_bar():
    """토요일(또는 공휴일·장 시작 전·적재 지연): 마지막 봉의 변동과 그 날짜가 카드·계좌 행에 실린다."""
    from app.dashboard import kst_today

    today = kst_today()
    d_prev, d_last = today - timedelta(days=3), today - timedelta(days=2)   # 오늘 봉 없음
    code = "AW" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"주말{code}", {d_prev: 100_000, d_last: 110_000})
    c, h, pid = _port_with_holding(code, 10, 90_000, today - timedelta(days=5))

    d = c.get("/dashboard", headers=h).json()
    kr = d["kr_stock"]
    assert kr["day_change"] == 10 * (110_000 - 100_000)          # 마지막 거래일의 변동 (종전에는 0)
    assert abs(kr["day_change_pct"] - 0.1) < 1e-9
    assert kr["day_change_asof"] == d_last.isoformat()           # 화면에 표기할 기준일
    row = next(p for p in d["portfolios"] if p["id"] == pid)
    assert row["day_change"] == 100_000 and row["day_change_asof"] == d_last.isoformat()


def test_dashboard_total_card_compares_snapshots_of_the_last_bar_day():
    """총자산 카드도 같다 — 마지막 봉 날짜의 스냅샷과 그 직전 스냅샷을 비교한다(오늘 스냅샷은 값이 같아 0 이 된다)."""
    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import AssetSnapshot, TradePortfolio

    today = kst_today()
    d_prev, d_last = today - timedelta(days=3), today - timedelta(days=2)
    code = "AT" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"총자산{code}", {d_prev: 100_000, d_last: 110_000})
    c, h, pid = _port_with_holding(code, 10, 90_000, today - timedelta(days=5))
    with SessionLocal() as s:
        uid = s.get(TradePortfolio, pid).user_id
        # 그날의 종가로 평가된 스냅샷을 그날 날짜로 남긴다 (배치가 매일 하는 일)
        for day, px in ((d_prev, 100_000), (d_last, 110_000)):
            with patch("app.dashboard.latest_closes", return_value={
                    s.scalar(select(__import__("app.models", fromlist=["Instrument"]).Instrument.id)
                             .where(__import__("app.models", fromlist=["Instrument"]).Instrument.code == code)): float(px)}):
                compute_user_snapshot(s, uid, day)
        s.commit()
        totals = {r.snap_date: int(r.total) for r in s.scalars(select(AssetSnapshot).where(
            AssetSnapshot.user_id == uid)).all()}

    d = c.get("/dashboard", headers=h).json()
    assert d["change_asof"] == d_last.isoformat()
    assert d["change_amount"] == totals[d_last] - totals[d_prev] == 10 * (110_000 - 100_000)


def test_dashboard_keeps_today_as_the_basis_when_a_bar_exists_for_today():
    """오늘 봉이 있으면(장 마감 후) 종전과 같다 — 기준일 None, 오늘 종가 vs 전일 종가."""
    from app.dashboard import kst_today

    today = kst_today()
    y = today - timedelta(days=1)
    code = "AN" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"오늘{code}", {y: 100_000, today: 110_000})
    c, h, pid = _port_with_holding(code, 10, 90_000, today - timedelta(days=5))

    d = c.get("/dashboard", headers=h).json()
    assert d["kr_stock"]["day_change"] == 100_000
    assert d["kr_stock"]["day_change_asof"] is None              # 오늘 것이라 날짜 표기 없음
    assert d["change_asof"] is None


def test_journal_day_change_falls_back_to_the_last_trading_day(monkeypatch):
    """매매일지 행도 같은 규칙 — 표시가가 마지막 종가와 같으면 그 직전 종가와 비교하고 기준일을 남긴다."""
    import app.mjournal as mj
    from app.dashboard import kst_today

    class _NoKis:
        def fetch_daily(self, code, a, b, org_price=True):
            return []

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: _NoKis())
    mj._PRICE_CACHE.clear()
    mj._CLOSE_MISS.clear()

    today = kst_today()
    d_prev, d_last = today - timedelta(days=3), today - timedelta(days=2)
    code = "AJ" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"일지{code}", {d_prev: 10_000, d_last: 11_000}, type_="STOCK")

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "기준일 일지", "symbol": f"일지{code}", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    assert c.post(f"/mjournals/{jid}/entries",
                  json={"side": "buy", "qty": 10, "price": 9_000, "trade_date": (today - timedelta(days=5)).isoformat(),
                        "symbol": f"일지{code}", "code": code}, headers=h).status_code == 201

    row = next(j for j in c.get("/dashboard", headers=h).json()["journals"] if j["id"] == jid)
    assert row["day_change"] == 10 * (11_000 - 10_000)           # 마지막 거래일의 변동 (종전에는 0)
    assert abs(row["day_change_pct"] - 0.1) < 1e-9
