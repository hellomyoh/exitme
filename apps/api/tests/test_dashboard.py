"""대시보드 테스트 — 스냅샷 합 일치·전일대비·추이·캘린더·기타 자산 격리·이벤트 (feature-dashboard §12)."""
import uuid
from datetime import date, datetime, timedelta, timezone

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


@pytest.fixture(autouse=True)
def seeded():
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")


def _client_token():
    client = TestClient(app, base_url="https://testserver")
    email = f"db{uuid.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_dashboard_total_is_sum_of_components():
    client, h = _client_token()
    ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"kind": "deposit", "amount": 50_000_000, "executed_at": ts}, headers=h)
    client.post("/positions", json={"kind": "buy", "code": "069500", "qty": 100, "price": 70000,
                                    "executed_at": ts}, headers=h)
    client.post("/manual-assets", json={"name": "정기예금", "category": "예금", "value": 30_000_000}, headers=h)
    d = client.get("/dashboard", headers=h).json()
    assert d["total"] == d["stock"] + d["cash"] + d["other"]
    assert d["cash"] == 50_000_000 - 100 * 70000
    assert d["other"] == 30_000_000
    assert d["stock"] > 0  # 최신 종가 평가


def test_change_vs_yesterday():
    client, h = _client_token()
    client.post("/positions", json={"kind": "deposit", "amount": 10_000_000,
                                    "executed_at": datetime(2025, 1, 2, tzinfo=timezone.utc).isoformat()}, headers=h)
    # 전일 스냅샷을 수동 주입 (배치가 적재했다고 가정)
    from app.dashboard import compute_user_snapshot
    from app.models import AssetSnapshot, User

    with SessionLocal() as s:
        email_user = s.scalars(select(User).order_by(User.id.desc())).first()
        snap = compute_user_snapshot(s, email_user.id, date.today() - timedelta(days=1))
        snap.total = snap.total - 500_000  # 어제는 50만원 적었다고 설정
        s.commit()
    d = client.get("/dashboard", headers=h).json()
    assert d["change_amount"] == 500_000
    assert d["change_pct"] == pytest.approx(500_000 / (d["total"] - 500_000), rel=1e-6)


def test_trend_and_calendar():
    client, h = _client_token()
    client.post("/positions", json={"kind": "deposit", "amount": 1_000_000,
                                    "executed_at": datetime(2025, 1, 2, tzinfo=timezone.utc).isoformat()}, headers=h)
    from app.dashboard import compute_user_snapshot
    from app.models import User

    with SessionLocal() as s:
        u = s.scalars(select(User).order_by(User.id.desc())).first()
        for i in range(5, 0, -1):
            compute_user_snapshot(s, u.id, date.today() - timedelta(days=i))
        s.commit()
    t = client.get("/portfolio/trend?range_=1M", headers=h).json()
    assert len(t["items"]) >= 5
    month = date.today().strftime("%Y-%m")
    c = client.get(f"/portfolio/calendar?month={month}", headers=h).json()
    assert isinstance(c["items"], list)
    assert client.get("/portfolio/trend?range_=XX", headers=h).status_code == 422


def test_manual_assets_crud_and_isolation():
    client, h1 = _client_token()
    client.cookies.clear()
    _, h2 = _client_token()
    mid = client.post("/manual-assets", json={"name": "금", "category": "원자재", "value": 5_000_000},
                      headers=h1).json()["id"]
    # 타인 수정/삭제 404
    assert client.patch(f"/manual-assets/{mid}", json={"name": "금", "category": "원자재", "value": 1},
                        headers=h2).status_code == 404
    assert client.delete(f"/manual-assets/{mid}", headers=h2).status_code == 404
    # 본인 수정·삭제
    assert client.patch(f"/manual-assets/{mid}", json={"name": "금", "category": "원자재", "value": 6_000_000},
                        headers=h1).status_code == 200
    assert client.delete(f"/manual-assets/{mid}", headers=h1).status_code == 200


def test_analytics_events_recorded():
    client, h = _client_token()
    client.get("/dashboard", headers=h)
    client.post("/backtests", json={"capital": 50_000_000, "date_from": "2024-01-02",
                                    "date_to": "2025-01-02"}, headers=h)
    from app.models import AnalyticsEvent

    with SessionLocal() as s:
        kinds = {k for (k,) in s.execute(select(AnalyticsEvent.kind))}
        assert "visit" in kinds and "backtest_run" in kinds


# ── 2026-09-02 자산 구분·포트별 스냅샷 (feature-dashboard §12, ADR-008)

def _me(s):
    from app.models import User
    return s.scalars(select(User).order_by(User.id.desc())).first()


def test_snapshot_invariant_and_unique_upsert():
    """합산 불변식: Σ(KRW 포트 equity) + other == total. 중복 열람 → (pid, date) 1행."""
    from app.dashboard import kst_today
    from app.models import AssetSnapshot, PortfolioSnapshot, TradePortfolio

    client, h = _client_token()
    ts = datetime(2025, 1, 10, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"kind": "deposit", "amount": 50_000_000, "executed_at": ts}, headers=h)
    client.post("/positions", json={"kind": "buy", "code": "069500", "qty": 100, "price": 70000,
                                    "executed_at": ts}, headers=h)
    client.post("/manual-assets", json={"name": "예금", "category": "예금", "value": 5_000_000}, headers=h)
    client.get("/dashboard", headers=h)
    client.get("/dashboard", headers=h)  # 같은 날 재열람 — ON CONFLICT 갱신이어야 함
    with SessionLocal() as s:
        user = _me(s)
        pids = s.scalars(select(TradePortfolio.id).where(TradePortfolio.user_id == user.id)).all()
        snaps = s.scalars(select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id.in_(pids),
            PortfolioSnapshot.snap_date == kst_today())).all()
        assert len(snaps) == len({sn.portfolio_id for sn in snaps})  # 포트당 1행
        total = s.scalar(select(AssetSnapshot).where(
            AssetSnapshot.user_id == user.id, AssetSnapshot.snap_date == kst_today()))
        kr_sum = sum(sn.equity for sn in snaps if sn.currency == "KRW")
        assert kr_sum + total.other == total.total  # ADR-008 불변식


def test_kr_us_cards_no_currency_mixing():
    """KR(원)·US(센트) 혼재 사용자 — 카드 상호 미혼입, 총자산은 KR 한정, series 에 USD 표기."""
    client, h = _client_token()
    ts = datetime(2026, 8, 20, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"kind": "deposit", "amount": 10_000_000, "executed_at": ts}, headers=h)
    client.post("/positions", json={"kind": "buy", "code": "069500", "qty": 10, "price": 70000,
                                    "executed_at": ts}, headers=h)
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars
    with SessionLocal() as s:
        inst = get_or_create_instrument(s, "QQQ", "QQQ", "NASDAQ")
        upsert_daily_bars(s, inst.id, [{"trade_date": date(2026, 8, 20), "open": 40000, "high": 41000,
                                        "low": 39500, "close": 40500, "volume": 100}], source="pykrx")
        s.commit()
    pid = client.post("/portfolios", json={"name": "us", "market": "US"}, headers=h).json()["id"]
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 100_000,
                                    "executed_at": ts}, headers=h)
    r = client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "QQQ", "qty": 2,
                                        "price": 40000, "executed_at": ts}, headers=h)
    assert r.status_code == 201
    d = client.get("/dashboard", headers=h).json()
    assert d["kr_stock"]["cost"] == 700_000          # 원 단위 — 센트 미혼입
    assert d["us_stock"]["cost"] == 80_000           # 센트 단위 — 원 미혼입
    assert d["us_stock"]["value"] == 2 * 40500
    assert d["stock"] == d["kr_stock"]["value"]      # 총자산 구성은 KR 한정 유지
    t = client.get("/portfolio/trend?range_=1M", headers=h).json()
    curr = {sr["portfolio_id"]: sr["currency"] for sr in t["series"]}
    assert curr[pid] == "USD"


def test_tx_delete_refreshes_today_snapshot():
    """거래 삭제 후 당일 스냅샷 재계산 — trend 유령값 방지 (검토 Q1)."""
    from app.dashboard import kst_today

    client, h = _client_token()
    ts = datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat()
    tx = client.post("/positions", json={"kind": "deposit", "amount": 1_000_000,
                                         "executed_at": ts}, headers=h).json()
    client.get("/dashboard", headers=h)  # 오늘 스냅샷 적재 (1,000,000)
    client.delete(f"/positions/{tx['id']}", headers=h)
    t = client.get("/portfolio/trend?range_=1M", headers=h).json()
    today_pt = [i for i in t["items"] if i["date"] == kst_today().isoformat()]
    assert today_pt and today_pt[0]["total"] == 0
    s = client.get("/portfolio/summary", headers=h).json()
    assert s["total_equity"] == today_pt[0]["total"]


def test_portfolio_delete_cascades_snapshots():
    """스냅샷 보유 포트 삭제 — FK CASCADE 로 200 (검토 D1)."""
    client, h = _client_token()
    pid = client.post("/portfolios", json={"name": "temp", "market": "KR"}, headers=h).json()["id"]
    ts = datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat()
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1000,
                                    "executed_at": ts}, headers=h)
    client.get("/dashboard", headers=h)  # 포트 스냅샷 적재
    assert client.delete(f"/portfolios/{pid}", headers=h).status_code == 200
