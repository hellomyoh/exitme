"""매매일지 일별 스냅샷 (0028, 2026-09-12 지시) — 자산 추이에서 일지를 하나씩 선으로 그린다.

종전에는 사용자 합계 한 칸(asset_snapshots.journal)만 있어 일지별 과거 값이 없었고 차트도 합계 한 줄이었다.
포트(portfolio_snapshots)와 같은 구조의 테이블을 두고, 매일 스냅샷과 함께 적재한다.
과거는 기록·종가로 되살린 근사(approx=True)이며 그 표시는 차트가 알린다.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
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


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"js{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _seed(code: str, name: str, closes: dict[date, int]) -> None:
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, name, "KOSPI", type_="STOCK")
        with patch("app.services.ingest.market_session_state", return_value=(max(closes), True)):
            upsert_daily_bars(s, inst.id, [{"trade_date": d, "open": v, "high": v, "low": v, "close": v, "volume": 1}
                                           for d, v in closes.items()], source="kis")
        s.commit()


def _journal(c, h, name: str, symbol: str, code: str, qty: int, price: int, day: date) -> int:
    jid = c.post("/mjournals", json={"name": name, "symbol": symbol, "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    assert c.post(f"/mjournals/{jid}/entries",
                  json={"side": "buy", "qty": qty, "price": price, "trade_date": day.isoformat(),
                        "symbol": symbol, "code": code}, headers=h).status_code == 201
    return jid


@pytest.fixture()
def _no_kis(monkeypatch):
    import app.mjournal as mj

    class _NoKis:
        def fetch_daily(self, code, a, b, org_price=True):
            return []

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: _NoKis())
    mj._PRICE_CACHE.clear()
    mj._CLOSE_MISS.clear()


def test_snapshot_batch_writes_one_row_per_journal(_no_kis):
    """대시보드 열람(스냅샷 갱신)마다 일지별 행이 쌓인다 — 값은 총자산에 넣는 평가액과 같다."""
    from app.dashboard import kst_today
    from app.models import JournalSnapshot, ManualJournal

    today = kst_today()
    code_a, code_b = "JA" + uuid.uuid4().hex[:4].upper(), "JB" + uuid.uuid4().hex[:4].upper()
    _seed(code_a, f"가{code_a}", {today - timedelta(days=1): 10_000, today: 11_000})
    _seed(code_b, f"나{code_b}", {today - timedelta(days=1): 20_000, today: 21_000})

    c, h = _client()
    ja = _journal(c, h, "일지A", f"가{code_a}", code_a, 10, 9_000, today - timedelta(days=3))
    jb = _journal(c, h, "일지B", f"나{code_b}", code_b, 5, 19_000, today - timedelta(days=3))

    rows = c.get("/dashboard", headers=h).json()["journals"]
    by_id = {r["id"]: r for r in rows}
    with SessionLocal() as s:
        uid = s.scalar(select(ManualJournal.user_id).where(ManualJournal.id == ja))
        snaps = {r.journal_id: r for r in s.scalars(select(JournalSnapshot).where(
            JournalSnapshot.snap_date == today,
            JournalSnapshot.journal_id.in_([ja, jb]))).all()}
    assert set(snaps) == {ja, jb}
    assert int(snaps[ja].value) == by_id[ja]["value"] == 10 * 11_000
    assert int(snaps[jb].value) == by_id[jb]["value"] == 5 * 21_000
    assert int(snaps[ja].cost) == 10 * 9_000
    assert all(r.counted and not r.approx for r in snaps.values())   # 배치분은 정확값


def test_trend_returns_one_series_per_journal(_no_kis):
    """자산 추이가 일지별로 선을 준다 — 이름·journal_id 가 실리고 kind 는 journal."""
    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import ManualJournal

    today = kst_today()
    code = "JT" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"추이{code}", {today - timedelta(days=2): 10_000, today - timedelta(days=1): 10_500, today: 11_000})
    c, h = _client()
    ja = _journal(c, h, "추이일지A", f"추이{code}", code, 10, 9_000, today - timedelta(days=5))
    jb = _journal(c, h, "추이일지B", f"추이{code}", code, 4, 9_500, today - timedelta(days=5))
    with SessionLocal() as s:
        uid = s.scalar(select(ManualJournal.user_id).where(ManualJournal.id == ja))
        for d in (today - timedelta(days=1), today):
            compute_user_snapshot(s, uid, d)
        s.commit()

    series = c.get("/portfolio/trend?range_=3M", headers=h).json()["series"]
    js = {x["name"]: x for x in series if x.get("kind") == "journal"}
    assert set(js) == {"추이일지A", "추이일지B"}                       # 합계 한 줄이 아니라 일지별
    assert js["추이일지A"]["journal_id"] == ja and js["추이일지B"]["journal_id"] == jb
    assert len(js["추이일지A"]["points"]) >= 2
    assert all(not x.get("approx") for x in js.values())              # 배치분만이면 근사 아님


def test_trend_falls_back_to_the_aggregate_line_without_journal_snapshots(_no_kis):
    """일지 스냅샷이 하나도 없는 사용자(적재 전)는 종전처럼 사용자 합계 한 줄을 받는다."""
    from app.dashboard import kst_today
    from app.models import AssetSnapshot, User

    today = kst_today()
    c, h = _client()
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.email.like("js%")).order_by(User.id.desc()))
        for i, v in ((2, 1_000_000), (1, 1_200_000)):
            s.add(AssetSnapshot(user_id=uid, snap_date=today - timedelta(days=i), total=v + 5_000_000,
                                stock=0, cash=5_000_000, other=0, journal=v))
        s.commit()
    series = c.get("/portfolio/trend?range_=3M", headers=h).json()["series"]
    js = [x for x in series if x.get("kind") == "journal"]
    assert len(js) == 1 and js[0]["name"] == "매매일지" and js[0].get("journal_id") is None


def test_backfill_recreates_history_from_entries_and_marks_it_approximate(_no_kis):
    """소급 적재: 기록·종가로 과거 일별 값을 되살리고 approx 로 표시한다. 기존(정확) 행은 건드리지 않는다."""
    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import JournalSnapshot, ManualJournal
    from scripts.backfill_journal_snapshots import run

    today = kst_today()
    d0 = today - timedelta(days=6)
    code = "JF" + uuid.uuid4().hex[:4].upper()
    _seed(code, f"소급{code}", {d0: 10_000, d0 + timedelta(days=2): 12_000, today: 15_000})
    c, h = _client()
    jid = _journal(c, h, "소급일지", f"소급{code}", code, 10, 9_000, d0)
    with SessionLocal() as s:
        uid = s.scalar(select(ManualJournal.user_id).where(ManualJournal.id == jid))
        compute_user_snapshot(s, uid, today)          # 오늘은 배치분(정확)
        s.commit()

    out = run(days=30, dry_run=False, overwrite_approx=False)
    assert out["written"] > 0

    with SessionLocal() as s:
        rows = {r.snap_date: r for r in s.scalars(select(JournalSnapshot)
                                                  .where(JournalSnapshot.journal_id == jid)).all()}
    assert rows[today].approx is False                               # 배치분 보존
    assert int(rows[today].value) == 10 * 15_000
    assert rows[d0].approx is True and int(rows[d0].value) == 10 * 10_000       # 매수 당일 종가
    assert int(rows[d0 + timedelta(days=1)].value) == 10 * 10_000               # 봉 없는 날은 직전 종가
    assert int(rows[d0 + timedelta(days=2)].value) == 10 * 12_000
