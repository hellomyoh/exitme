"""스냅샷 정합 (2026-09-09 사고 — 매도 등록 직후 당일 스냅샷이 '로트는 매도 후, 현금은 매도 전' 반쪽으로 저장) —
등록·삭제 직후 저장된 스냅샷 = 커밋 뒤 재계산값, 과거 날짜 as-of 보정 도구. DB 필요."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

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
    tok = c.post("/auth/register", json={"email": f"sc{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _instrument_with_bars(closes: dict[date, int]) -> str:
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    code = "S" + uuid.uuid4().hex[:5].upper()
    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, f"정합{code}", "KOSPI", type_="ETF")
        upsert_daily_bars(s, inst.id, [{"trade_date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1} for d, c in closes.items()], source="kis")
        s.commit()
    return code


def _pf_snap(pid: int, day: date) -> tuple[int, int] | None:
    from app.models import PortfolioSnapshot

    with SessionLocal() as s:
        r = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pid, PortfolioSnapshot.snap_date == day))
        return (int(r.stock_value), int(r.cash)) if r else None


def test_same_day_snapshot_matches_ledger_right_after_register_and_delete():
    """입금·매수·매도·삭제 등록 직후 저장된 당일 스냅샷이 원장과 같다 (종전: 한 거래씩 늦게 반영 — flush 누락)."""
    from app.dashboard import kst_today

    today = kst_today()
    code = _instrument_with_bars({today: 110_660})
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "정합", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]

    def tx(body):
        r = c.post("/positions", json={"portfolio_id": pid, **body}, headers=h)
        assert r.status_code in (200, 201), r.text
        return r.json()

    tx({"kind": "deposit", "amount": 100_000_000, "executed_at": (today - timedelta(days=8)).isoformat() + "T10:00:00+09:00"})
    assert _pf_snap(pid, today) == (0, 100_000_000)                          # 종전 (0, 0)
    tx({"kind": "buy", "code": code, "qty": 666, "price": 100_000, "executed_at": (today - timedelta(days=7)).isoformat() + "T10:00:00+09:00"})
    assert _pf_snap(pid, today) == (666 * 110_660, 33_400_000)              # 종전 (0, 100,000,000)
    sell = tx({"kind": "sell", "code": code, "qty": 30, "price": 114_000, "executed_at": today.isoformat() + "T21:42:00+09:00"})
    assert _pf_snap(pid, today) == (636 * 110_660, 36_820_000)              # 종전 (636주 평가, 33,400,000) — 매도 대금 3,420,000 누락
    # 대시보드 열람(재계산)과 같은 값 — 반쪽 상태 없음
    assert c.get("/dashboard", headers=h).status_code == 200 and _pf_snap(pid, today) == (636 * 110_660, 36_820_000)
    # 삭제 직후도 원장과 같다 (재생 로트 flush)
    assert c.delete(f"/positions/{sell['id']}", headers=h).status_code == 200
    assert _pf_snap(pid, today) == (666 * 110_660, 33_400_000)


def test_snapshot_repair_recomputes_past_day_from_ledger():
    """보정 도구 — 어제 날짜 스냅샷이 반쪽(현금에 매도 대금 누락)으로 저장돼 있을 때 as-of 원장으로 되돌린다. journal·other 는 유지, dry-run 은 저장 없음."""
    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import AssetSnapshot, PortfolioSnapshot, TradePortfolio
    from app.services.snapshot_repair import recompute_snapshots_asof

    today = kst_today()
    y = today - timedelta(days=1)
    code = _instrument_with_bars({y - timedelta(days=1): 111_030, y: 110_660, today: 112_400})
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "보정", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    with SessionLocal() as s:
        uid = s.get(TradePortfolio, pid).user_id
    for body in ({"kind": "deposit", "amount": 100_000_000, "executed_at": (y - timedelta(days=5)).isoformat() + "T10:00:00+09:00"},
                 {"kind": "buy", "code": code, "qty": 666, "price": 100_000, "executed_at": (y - timedelta(days=4)).isoformat() + "T10:00:00+09:00"},
                 {"kind": "sell", "code": code, "qty": 30, "price": 114_000, "executed_at": y.isoformat() + "T21:42:00+09:00"}):
        assert c.post("/positions", json={"portfolio_id": pid, **body}, headers=h).status_code in (200, 201)
    # 어제 스냅샷을 서버와 같은 반쪽 상태로 심는다: 주식 636주×어제 종가, 현금은 매도 대금 3,420,000 빠진 값. journal 1,000,000 은 별도 자산
    with SessionLocal() as s:
        s.add(PortfolioSnapshot(portfolio_id=pid, snap_date=y, equity=636 * 110_660 + 33_400_000, stock_value=636 * 110_660, cash=33_400_000, currency="KRW"))
        s.add(AssetSnapshot(user_id=uid, snap_date=y, total=636 * 110_660 + 33_400_000 + 1_000_000, stock=636 * 110_660, cash=33_400_000, other=0, journal=1_000_000))
        s.commit()
        compute_user_snapshot(s, uid, today)
        s.commit()
    with SessionLocal() as s:
        dry = recompute_snapshots_asof(s, uid, y, apply=False)
        s.commit()
    assert dry["applied"] is False and dry["portfolios"][0]["after"] == {"stock": 636 * 110_660, "cash": 36_820_000} and dry["portfolios"][0]["diff"] == 3_420_000
    assert dry["asset"]["diff"] == 3_420_000 and dry["asset"]["after"]["journal"] == 1_000_000
    assert _pf_snap(pid, y) == (636 * 110_660, 33_400_000)                   # dry-run 은 저장하지 않는다
    with SessionLocal() as s:
        res = recompute_snapshots_asof(s, uid, y, apply=True)
        s.commit()
    assert res["applied"] is True and _pf_snap(pid, y) == (636 * 110_660, 36_820_000)
    with SessionLocal() as s:
        a = s.scalar(select(AssetSnapshot).where(AssetSnapshot.user_id == uid, AssetSnapshot.snap_date == y))
        assert int(a.total) == 636 * 110_660 + 36_820_000 + 1_000_000 and int(a.journal) == 1_000_000 and int(a.cash) == 36_820_000
    # 보정 뒤 대시보드 전일 대비 = 가격 변동(636 × 1,740) − 어제 심어 둔 journal 1,000,000(오늘은 일지가 없어 0) — 매도 대금 3,420,000 은 손익에 없다
    d = c.get("/dashboard", headers=h).json()
    assert d["change_amount"] == 636 * (112_400 - 110_660) - 1_000_000
